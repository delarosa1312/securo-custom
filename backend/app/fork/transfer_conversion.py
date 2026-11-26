"""Existing individually scheduled transfers as candidate recurring transfers (F-004).

Preview only reads. A candidate series is a group of future transfer pairs, none linked to a
schedule, with the same source, destination, amounts and description, whose dates follow one
frequency (with or without a weekend adjustment). Apply, one series per explicit request, creates
the transfer schedule and links the existing pairs to it as its generated occurrences; no row
changes except its link, so no balance or forecast moves. Nothing here runs by itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.app_clock import app_today
from app.fork import recurring_transfers
from app.fork.models import RecurringOccurrence
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction

_FREQUENCIES = ("monthly", "weekly", "biweekly", "quarterly", "semiannual", "yearly")
_WEEKEND = ("none", "previous_friday", "next_monday")
_SERIES_NAMESPACE = uuid.UUID("6f0c5a52-3c55-4a4f-9a54-1f6e0b7f2a04")

STALE_SERIES = "This series changed since it was previewed (or does not exist): preview again"


class ConversionConflict(Exception):
    """The series is gone or changed, or the schedule would duplicate an existing one."""


@dataclass
class Pair:
    pair_id: uuid.UUID
    debit: Transaction
    credit: Transaction


@dataclass
class Series:
    series_id: uuid.UUID
    description: str
    source: Account
    destination: Account
    amount: Decimal
    destination_amount: Optional[Decimal]
    frequency: str
    weekend_adjustment: str
    day_of_month: int
    start_date: date  # nominal date of the first pair
    nominal_dates: list[date]
    pairs: list[Pair] = field(default_factory=list)

    @property
    def last_nominal(self) -> date:
        return self.nominal_dates[-1]


@dataclass
class Preview:
    series: list[Series]
    # Future unlinked transfer pairs that are not in any series (alone, or dates without a rhythm).
    left_alone: int


def _normalized(description: str) -> str:
    return " ".join((description or "").lower().split())


def _fit(dates: list[date]) -> Optional[tuple[str, str, date, list[date]]]:
    """The first schedule (frequency, weekend adjustment, nominal start) producing exactly ``dates``."""
    from app.services.recurring_transaction_service import _advance_date, adjust_weekend_date

    first = dates[0]
    for weekend in _WEEKEND:
        # An adjusted first date was nominally up to two days earlier or later.
        starts = [first] if weekend == "none" else [first + timedelta(days=k) for k in (0, -2, -1, 1, 2)]
        for frequency in _FREQUENCIES:
            for nominal_start in starts:
                if weekend != "none" and adjust_weekend_date(nominal_start, weekend) != first:
                    continue
                nominal, produced, current = [], [], nominal_start
                for _ in dates:
                    nominal.append(current)
                    produced.append(adjust_weekend_date(current, weekend))
                    current = _advance_date(current, frequency, intended_day=nominal_start.day)
                if produced == dates:
                    return frequency, weekend, nominal_start, nominal
    return None


async def _future_unlinked_pairs(session: AsyncSession, workspace_id: uuid.UUID) -> list[Pair]:
    credit = aliased(Transaction)
    rows = (
        await session.execute(
            select(Transaction, credit)
            .join(credit, credit.transfer_pair_id == Transaction.transfer_pair_id)
            .where(
                Transaction.workspace_id == workspace_id,
                credit.workspace_id == workspace_id,
                Transaction.transfer_pair_id.is_not(None),
                Transaction.type == "debit",
                credit.type == "credit",
                Transaction.date > app_today(),
                Transaction.recurring_transaction_id.is_(None),
                credit.recurring_transaction_id.is_(None),
            )
            .order_by(Transaction.date, Transaction.id)
        )
    ).all()
    seen: set[uuid.UUID] = set()
    pairs = []
    for debit, credit_row in rows:
        pair_id = debit.transfer_pair_id
        # A pair id shared by more than two rows is not a clean pair: leave it out.
        if pair_id in seen:
            pairs = [p for p in pairs if p.pair_id != pair_id]
            continue
        seen.add(pair_id)
        pairs.append(Pair(pair_id, debit, credit_row))
    return pairs


async def preview(session: AsyncSession, workspace_id: uuid.UUID) -> Preview:
    pairs = await _future_unlinked_pairs(session, workspace_id)
    accounts = {
        account.id: account
        for account in (
            await session.execute(
                select(Account).where(
                    Account.id.in_({p.debit.account_id for p in pairs} | {p.credit.account_id for p in pairs})
                )
            )
        ).scalars().all()
    }
    groups: dict[tuple, list[Pair]] = {}
    for pair in pairs:
        key = (
            pair.debit.account_id, pair.credit.account_id, pair.debit.amount, pair.credit.amount,
            _normalized(pair.debit.description),
        )
        groups.setdefault(key, []).append(pair)

    series: list[Series] = []
    left_alone = 0
    for (source_id, destination_id, amount, received, _), members in groups.items():
        source, destination = accounts.get(source_id), accounts.get(destination_id)
        dates = [p.debit.date for p in members]
        fitted = _fit(dates) if len(members) >= 2 and len(set(dates)) == len(dates) else None
        if (
            fitted is None
            or source is None
            or destination is None
            or source.is_closed
            or destination.is_closed
            or source_id == destination_id
        ):
            left_alone += len(members)
            continue
        frequency, weekend, nominal_start, nominal = fitted
        cross = source.currency != destination.currency
        ids = sorted(str(p.pair_id) for p in members)
        series.append(
            Series(
                series_id=uuid.uuid5(_SERIES_NAMESPACE, ",".join(ids)),
                description=members[0].debit.description,
                source=source,
                destination=destination,
                amount=amount,
                destination_amount=received if cross else None,
                frequency=frequency,
                weekend_adjustment=weekend,
                day_of_month=nominal_start.day,
                start_date=nominal_start,
                nominal_dates=nominal,
                pairs=members,
            )
        )
    series.sort(key=lambda s: (s.start_date, s.description))
    return Preview(series=series, left_alone=left_alone)


async def apply(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    series_id: uuid.UUID,
    *,
    end_date: Optional[date] = None,
    open_ended: bool = False,
) -> tuple[RecurringTransaction, int]:
    """Create the schedule for one previewed series and link its pairs. Commits.

    The schedule ends at the series' last transfer unless ``end_date`` (on or after it) or
    ``open_ended`` says otherwise; its pointer moves past the linked transfers, so generation
    writes nothing for them.
    """
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.fx_rate_service import stamp_primary_amount
    from app.services.recurring_transaction_service import (
        DUPLICATE_RECURRING_MESSAGE,
        _advance_date,
        _find_duplicate_recurring,
    )

    found = next((s for s in (await preview(session, workspace_id)).series if s.series_id == series_id), None)
    if found is None:
        raise ConversionConflict(STALE_SERIES)
    if open_ended and end_date is not None:
        raise ValueError("Give an end date or open_ended, not both")
    if end_date is not None and end_date < found.last_nominal:
        raise ValueError(f"The end date cannot be before the series' last transfer ({found.last_nominal})")
    rows = [row for pair in found.pairs for row in (pair.debit, pair.credit)]
    # Lock the rows and check nobody linked them since the preview.
    locked = (
        await session.execute(
            select(Transaction).where(Transaction.id.in_([row.id for row in rows])).with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    if len(locked) != len(rows) or any(row.recurring_transaction_id is not None for row in locked):
        raise ConversionConflict(STALE_SERIES)

    data = RecurringTransactionCreate.model_validate(
        {
            "description": found.description,
            "amount": found.amount,
            "currency": found.source.currency,
            "type": "debit",
            "frequency": found.frequency,
            "weekend_adjustment": found.weekend_adjustment,
            "day_of_month": found.day_of_month,
            "start_date": found.start_date,
            "end_date": None if open_ended else (end_date or found.last_nominal),
            "account_id": found.source.id,
            "destination_account_id": found.destination.id,
            "destination_amount": found.destination_amount,
        }
    )
    await recurring_transfers.check_create(session, workspace_id, data)
    if await _find_duplicate_recurring(
        session, workspace_id, user_id, data.description, data.frequency, data.start_date
    ) is not None:
        raise ConversionConflict(DUPLICATE_RECURRING_MESSAGE)
    schedule = RecurringTransaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=data.account_id,
        destination_account_id=data.destination_account_id,
        destination_amount=data.destination_amount,
        description=data.description,
        amount=data.amount,
        currency=data.currency,
        type="debit",
        frequency=data.frequency,
        weekend_adjustment=data.weekend_adjustment,
        day_of_month=data.day_of_month,
        start_date=data.start_date,
        end_date=data.end_date,
        auto_generate=True,
        next_occurrence=_advance_date(found.last_nominal, found.frequency, intended_day=found.day_of_month),
    )
    session.add(schedule)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConversionConflict(DUPLICATE_RECURRING_MESSAGE) from exc
    for pair, nominal in zip(found.pairs, found.nominal_dates):
        pair.debit.recurring_transaction_id = schedule.id
        pair.credit.recurring_transaction_id = schedule.id
        session.add(
            RecurringOccurrence(
                workspace_id=workspace_id,
                recurring_transaction_id=schedule.id,
                occurrence_date=nominal,
                state="generated",
                transaction_id=pair.debit.id,
            )
        )
    await stamp_primary_amount(session, user_id, schedule, date_field="start_date")
    await session.commit()
    await session.refresh(schedule)
    return schedule, len(found.pairs)
