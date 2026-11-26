"""Idempotent recurring generation (F-004), for every schedule type.

Upstream's `generate_pending` reads due schedules without a lock and advances each pointer in
memory, so two runs (the hourly job and the Generate button, or two workers) can both write the
same occurrence. The hooks there call this module to:

- lock each schedule row before its loop and re-read its pointer (`lock_schedule`);
- claim each occurrence in `recurring_occurrences`, unique per schedule and nominal date, before
  anything is written (`claim`), so an occurrence already written is never written again;
- write a transfer schedule's occurrence as one transfer pair, both legs or neither
  (`transfer_occurrence`).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.models import RecurringOccurrence
from app.fork.recurring_transfers import is_transfer_schedule, usable
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)


async def lock_schedule(session: AsyncSession, recurring: RecurringTransaction) -> bool:
    """Lock the schedule row until the run commits and refresh it from the database.

    A second run blocks here until the first commits, then reads the pointer the first one
    advanced and finds nothing due. False when the schedule is gone or no longer active.
    """
    row = (
        await session.execute(
            select(RecurringTransaction)
            .where(RecurringTransaction.id == recurring.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return row is not None and bool(row.is_active)


async def claim(
    session: AsyncSession, recurring: RecurringTransaction, nominal: date
) -> Optional[RecurringOccurrence]:
    """Record the occurrence as generated; None when it already was (by this run or another)."""
    exists = (
        await session.execute(
            select(RecurringOccurrence.id).where(
                RecurringOccurrence.recurring_transaction_id == recurring.id,
                RecurringOccurrence.occurrence_date == nominal,
            )
        )
    ).first()
    if exists is not None:
        return None
    try:
        async with session.begin_nested():
            occurrence = RecurringOccurrence(
                workspace_id=recurring.workspace_id,
                recurring_transaction_id=recurring.id,
                occurrence_date=nominal,
                state="generated",
            )
            session.add(occurrence)
            await session.flush()
    except IntegrityError:
        # Another run claimed it between the read and the insert.
        return None
    return occurrence


@dataclass
class Outcome:
    """What the hook in `generate_pending` does with one due occurrence.

    ``wait``: stop this schedule, pointer untouched. ``done``: nothing more to write, advance the
    pointer (``written`` rows counted). Otherwise upstream writes the occurrence and records the
    row on ``claimed``.
    """

    wait: bool = False
    done: bool = False
    written: int = 0
    claimed: Optional[RecurringOccurrence] = None


async def occurrence(
    session: AsyncSession,
    recurring: RecurringTransaction,
    user_id: uuid.UUID,
    effective_date: date,
) -> Outcome:
    nominal = recurring.next_occurrence
    if not is_transfer_schedule(recurring):
        claimed = await claim(session, recurring, nominal)
        if claimed is None:
            return Outcome(done=True)
        return Outcome(claimed=claimed)

    source = await session.get(Account, recurring.account_id)
    destination = await session.get(Account, recurring.destination_account_id)
    if not (usable(recurring, source) and usable(recurring, destination)):
        # Closed, gone or moved to another workspace: the occurrence waits, as upstream waits
        # for a bill whose account moved.
        return Outcome(wait=True)
    assert source is not None and destination is not None
    # Upstream's on-demand rate sync commits the session. It runs here, before the savepoint, so
    # the pair below is stamped from stored rates and stays one unit. A commit releases the
    # schedule lock early; the claim's unique key still keeps the occurrence single.
    await _warm_rates(session, user_id, effective_date, source.currency, destination.currency)
    try:
        async with session.begin_nested():
            claimed = await claim(session, recurring, nominal)
            if claimed is None:
                return Outcome(done=True)
            debit = await _write_pair(session, recurring, user_id, effective_date, source, destination)
            claimed.transaction_id = debit.id
            await session.flush()
    except Exception:
        # Both legs and the claim were rolled back together. The schedule waits for the next run
        # instead of stopping every other schedule's generation.
        logger.exception("Recurring transfer %s: occurrence %s not generated", recurring.id, nominal)
        return Outcome(wait=True)
    return Outcome(done=True, written=1)


async def _warm_rates(
    session: AsyncSession, user_id: uuid.UUID, on: date, *currencies: str
) -> None:
    from app.models.user import User
    from app.services.fx_rate_service import _resolve_rate

    user = await session.get(User, user_id)
    if user is None or not user.primary_currency:
        return
    for currency in set(currencies):
        if currency and currency != user.primary_currency:
            await _resolve_rate(session, currency, user.primary_currency, on)


async def _write_pair(
    session: AsyncSession,
    recurring: RecurringTransaction,
    user_id: uuid.UUID,
    effective_date: date,
    source: Account,
    destination: Account,
    *,
    amount: Optional[Decimal] = None,
    received: Optional[Decimal] = None,
) -> Transaction:
    """The occurrence's two legs, built like `transaction_service.create_transfer` builds a pair.

    Both legs carry the schedule's link (`source="recurring"`, as upstream's generated rows) and
    each follows its own account's rule for generated rows: pending on a bank-connected account,
    where the bank's own row is expected, posted on a manual one. ``amount`` and ``received``
    replace the schedule's figures for one occurrence edited on its own (F-005).
    """
    from app.services.credit_card_service import apply_effective_date
    from app.services.fx_rate_service import stamp_primary_amount
    from app.services.rule_service import apply_rules_to_transaction

    pair_id = uuid.uuid4()
    explicit = recurring.destination_amount is not None
    sent = amount if amount is not None else recurring.amount
    if received is None:
        received = recurring.destination_amount if explicit else sent
    legs: list[Transaction] = []
    for account, tx_type, leg_amount in (
        (source, "debit", sent),
        (destination, "credit", received),
    ):
        leg = Transaction(
            user_id=user_id,
            workspace_id=recurring.workspace_id,
            account_id=account.id,
            description=recurring.description,
            amount=leg_amount,
            currency=account.currency,
            date=effective_date,
            type=tx_type,
            source="recurring",
            status="pending" if account.connection_id is not None else "posted",
            transfer_pair_id=pair_id,
            transfer_amount_explicit=explicit,
            recurring_transaction_id=recurring.id,
        )
        apply_effective_date(leg, account)
        session.add(leg)
        legs.append(leg)
    await session.flush()
    debit, credit = legs
    for leg in legs:
        await apply_rules_to_transaction(session, user_id, leg)
    for leg in legs:
        # Never fetch inside the pair: the fetch commits (see `_warm_rates`).
        await stamp_primary_amount(session, user_id, leg, allow_fetch=False)
    # Both legs of a cross-currency pair show the same primary amount, as create_transfer does.
    if source.currency != destination.currency and debit.amount_primary is not None:
        credit.amount_primary = debit.amount_primary
        if credit.amount:
            credit.fx_rate_used = debit.amount_primary / Decimal(str(credit.amount))
    for leg in legs:
        if leg.status == "pending":
            # Same as upstream's pending placeholders: never hidden from the user by a rule.
            leg.is_ignored = False
    return debit


def record(outcome: Outcome, row: Transaction) -> None:
    """Note the row upstream wrote (or linked) for a claimed occurrence."""
    if outcome.claimed is not None:
        outcome.claimed.transaction_id = row.id


def advance(recurring: RecurringTransaction) -> None:
    """Upstream's pointer step at the end of its loop, for occurrences the hook finished."""
    from app.services.recurring_transaction_service import _advance_date

    recurring.next_occurrence = _advance_date(
        recurring.next_occurrence,
        recurring.frequency,
        intended_day=recurring.day_of_month or recurring.start_date.day,
    )
    if recurring.end_date and recurring.next_occurrence > recurring.end_date:
        recurring.is_active = False
