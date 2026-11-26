"""Rows a recurring series already wrote, sorted for a lifecycle action (F-005, issue #7).

history: posted and dated today or earlier, never changed; scheduled: pending or dated after today,
they follow the change or are deleted; edited: recorded ahead with their own values, kept.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.models import RecurringOccurrence
from app.fork.recurring_transfers import is_transfer_schedule
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction


@dataclass
class RowImpact:
    transaction_id: uuid.UUID
    date: date
    amount: Any
    status: str
    transfer_pair_id: Optional[uuid.UUID]


@dataclass
class Impact:
    """What an action does to the rows the series already wrote."""

    history: int = 0
    follow: list[RowImpact] = field(default_factory=list)
    removed: list[RowImpact] = field(default_factory=list)
    edited: list[RowImpact] = field(default_factory=list)
    skipped_kept: list[date] = field(default_factory=list)
    skipped_dropped: list[date] = field(default_factory=list)


def current_day(rec: RecurringTransaction) -> date:
    from app.core.app_clock import app_today

    return app_today()


async def rows_of(
    session: AsyncSession, recurring_ids: list[uuid.UUID], workspace_id: uuid.UUID
) -> list[Transaction]:
    return list(
        (
            await session.execute(
                select(Transaction)
                .where(
                    Transaction.workspace_id == workspace_id,
                    Transaction.recurring_transaction_id.in_(recurring_ids),
                )
                .order_by(Transaction.date, Transaction.id)
                .with_for_update()
            )
        ).scalars().all()
    )


async def ledger_by_tx(
    session: AsyncSession, recurring_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RecurringOccurrence]:
    rows = (
        await session.execute(
            select(RecurringOccurrence).where(
                RecurringOccurrence.recurring_transaction_id.in_(recurring_ids),
                RecurringOccurrence.transaction_id.is_not(None),
            )
        )
    ).scalars().all()
    return {row.transaction_id: row for row in rows if row.transaction_id}


def group_units(rows: list[Transaction]) -> list[list[Transaction]]:
    """Rows grouped as the user sees them: a transfer pair is one unit (debit leg first)."""
    pairs: dict[uuid.UUID, list[Transaction]] = {}
    units: list[list[Transaction]] = []
    for tx in rows:
        if tx.transfer_pair_id is None:
            units.append([tx])
            continue
        if tx.transfer_pair_id not in pairs:
            pairs[tx.transfer_pair_id] = []
            units.append(pairs[tx.transfer_pair_id])
        pairs[tx.transfer_pair_id].append(tx)
    for unit in units:
        unit.sort(key=lambda tx: tx.type != "debit")
    return units


def row_impact(unit: list[Transaction]) -> RowImpact:
    head = unit[0]
    return RowImpact(head.id, head.date, head.amount, head.status, head.transfer_pair_id)


def is_scheduled(tx: Transaction, today: date) -> bool:
    return tx.status == "pending" or tx.date > today


async def classify(
    session: AsyncSession,
    parts: list[RecurringTransaction],
    from_date: date,
    *,
    dates_change: bool,
    removing: bool = False,
    only_after_today: bool = False,
) -> tuple[Impact, list[list[Transaction]], list[list[Transaction]], list[list[Transaction]]]:
    """Sort the series' rows dated on or after ``from_date``: (impact, follow, remove, edited).

    ``removing`` (end, delete): scheduled rows are offered for deletion instead of following.
    ``only_after_today``: rows dated today or earlier are left alone (delete keeps what is due).
    """
    ids = [part.id for part in parts]
    workspace_id = parts[0].workspace_id
    today = current_day(parts[0])
    ledger = await ledger_by_tx(session, ids)
    impact = Impact()
    follow: list[list[Transaction]] = []
    remove: list[list[Transaction]] = []
    edited: list[list[Transaction]] = []
    for unit in group_units(await rows_of(session, ids, workspace_id)):
        head = unit[0]
        entry = next((ledger[tx.id] for tx in unit if tx.id in ledger), None)
        occurrence_day = entry.occurrence_date if entry is not None else head.date
        if occurrence_day < from_date and head.date < from_date:
            continue
        if only_after_today and head.date <= today:
            continue
        if not is_scheduled(head, today):
            impact.history += 1
            continue
        if removing:
            remove.append(unit)
            impact.removed.append(row_impact(unit))
        elif entry is not None and entry.state == "overridden" and not (dates_change and head.date > today):
            edited.append(unit)
            impact.edited.append(row_impact(unit))
        elif dates_change and head.date > today:
            remove.append(unit)
            impact.removed.append(row_impact(unit))
        else:
            follow.append(unit)
            impact.follow.append(row_impact(unit))
    return impact, follow, remove, edited


async def delete_units(session: AsyncSession, units: list[list[Transaction]]) -> None:
    from app.services.attachment_service import cleanup_attachment_files

    ids = [tx.id for unit in units for tx in unit]
    if not ids:
        return
    # A pair's other leg may not be linked (older rows): take it with its partner.
    pair_ids = {tx.transfer_pair_id for unit in units for tx in unit if tx.transfer_pair_id}
    if pair_ids:
        partners = (
            await session.execute(
                select(Transaction).where(
                    Transaction.transfer_pair_id.in_(pair_ids), Transaction.id.not_in(ids)
                )
            )
        ).scalars().all()
        units = [*units, list(partners)]
        ids += [tx.id for tx in partners]
    await session.execute(
        delete(RecurringOccurrence).where(RecurringOccurrence.transaction_id.in_(ids))
    )
    await cleanup_attachment_files(session, ids)
    for unit in units:
        for tx in unit:
            await session.delete(tx)
    await session.flush()


async def follow_change(
    session: AsyncSession, units: list[list[Transaction]], target: RecurringTransaction
) -> None:
    """Give scheduled rows the target schedule's values and link them to it (both legs)."""
    from app.services.credit_card_service import apply_effective_date
    from app.services.fx_rate_service import stamp_primary_amount
    from app.models.account import Account

    for unit in units:
        for tx in unit:
            tx.recurring_transaction_id = target.id
            tx.description = target.description
            if is_transfer_schedule(target) and tx.transfer_pair_id is not None:
                debit = tx.type == "debit"
                account_id = target.account_id if debit else target.destination_account_id
                received = target.destination_amount if target.destination_amount is not None else target.amount
                tx.amount = target.amount if debit else received
            else:
                account_id = target.account_id
                old_amount = tx.amount
                tx.amount = target.amount
                tx.type = target.type
                tx.currency = target.currency
                await follow_category(session, tx, target, old_amount)
            if account_id is not None and tx.account_id != account_id:
                tx.account_id = account_id
                account = await session.get(Account, account_id)
                if account is not None:
                    tx.currency = account.currency
                    apply_effective_date(tx, account)
            await session.flush()
            # Rates were warmed before anything was written: a fetch here would commit midway.
            await stamp_primary_amount(
                session, target.user_id, tx, allow_fetch=False, restamp_account=True
            )
    await session.execute(
        sql_update(RecurringOccurrence)
        .where(RecurringOccurrence.transaction_id.in_([tx.id for unit in units for tx in unit]))
        .values(recurring_transaction_id=target.id)
    )


async def follow_category(
    session: AsyncSession, tx: Transaction, target: RecurringTransaction, old_amount: Any
) -> None:
    """The row takes the schedule's category or split, sized to its amount."""
    from app.schemas.transaction_category_line import TransactionCategoryLinesInput
    from app.services import category_line_service

    if target.has_category_lines and len(target.category_lines) >= 2:
        lines = sorted(target.category_lines, key=lambda line: line.position)
        if lines[0].share_type == "percent":
            payload = {"share_type": "percent", "lines": [
                {"category_id": line.category_id, "share_pct": line.share_pct} for line in lines
            ]}
        else:
            try:
                amounts = category_line_service._distribute(
                    category_line_service._quantize(abs(tx.amount)),
                    category_line_service._weights(lines),
                )
            except ValueError:
                return
            payload = {"share_type": "exact", "lines": [
                {"category_id": line.category_id, "amount": amount}
                for line, amount in zip(lines, amounts)
            ]}
        await category_line_service.replace_lines(
            session, tx, TransactionCategoryLinesInput.model_validate(payload)
        )
        return
    if tx.has_category_lines:
        await category_line_service.replace_lines(
            session, tx, TransactionCategoryLinesInput(share_type="exact", lines=[])
        )
    tx.category_id = target.category_id




async def follow_edited(
    session: AsyncSession, units: list[list[Transaction]], target: RecurringTransaction
) -> None:
    """An occurrence edited on its own keeps its values and belongs to the new part."""
    ids = [tx.id for unit in units for tx in unit]
    if not ids:
        return
    for unit in units:
        for tx in unit:
            tx.recurring_transaction_id = target.id
    await session.execute(
        sql_update(RecurringOccurrence)
        .where(RecurringOccurrence.transaction_id.in_(ids))
        .values(recurring_transaction_id=target.id)
    )
