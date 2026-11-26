"""Recurring occurrences one at a time (F-005, issue #7): skip, record ahead, link, overdue.

Builds on the F-004 ledger (`recurring_occurrences`, unique per schedule and nominal date). A ledger
row means "dealt with": generation never writes that occurrence (`recurring_generation.claim`).

- ``skipped``: nothing is written; every projection leaves the occurrence out.
- ``overridden``: the occurrence was recorded ahead with its own amount or date (a real row now).
- ``generated``: recorded as the schedule says, by generation, by "record" or by linking a row.

Projections read rows, not the ledger; `handled_occurrences` is the one place they learn about
skipped and overridden occurrences (hooked into `dashboard_service._materialized_recurring_occurrences`).
Every write locks the schedule row first, the same lock generation takes, so the two serialize.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.models import RecurringOccurrence
from app.fork.recurring_transfers import is_transfer_schedule, usable
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction

# Ledger states that leave a projected occurrence out (generated ones are rows the views already see).
_HANDLED_ELSEWHERE = ("skipped", "overridden")
# Upper bound on the occurrences listed per schedule (a weekly schedule paused for years).
MAX_LISTED = 60

NOT_FOUND = "Recurring transaction not found"
NOT_AN_OCCURRENCE = "That date is not an occurrence of this recurring transaction"
ALREADY_HANDLED = "That occurrence is already recorded or skipped"
ALREADY_PASSED = "That occurrence is already behind the schedule; edit or delete its transaction instead"
NOT_SKIPPED = "That occurrence is not skipped"
DELETED = "This recurring transaction is deleted; restore it first"
ACCOUNT_UNUSABLE = "The account of this recurring transaction is closed or no longer in this workspace"
TRANSFER_LINK = "A recurring transfer cannot take an existing transaction as an occurrence"
LINK_MISMATCH = "The transaction must be on the same account, with the same direction, and not linked yet"
NEEDS_RECEIVED = "This transfer is between currencies: give the amount received as well"
POSITIVE = "The amount must be greater than zero"


class LifecycleConflict(Exception):
    """A request that contradicts the schedule's current state (409)."""


class LifecycleNotFound(Exception):
    """Unknown schedule or transaction in this workspace (404)."""


# --- dates --------------------------------------------------------------------------------------


def intended_day(rec: RecurringTransaction) -> int:
    return rec.day_of_month or rec.start_date.day


def effective(rec: RecurringTransaction, nominal: date) -> date:
    from app.services.recurring_transaction_service import adjust_weekend_date

    return adjust_weekend_date(nominal, rec.weekend_adjustment)


def nominal_dates(rec: RecurringTransaction, start: date, end: date, limit: int = 2000) -> list[date]:
    """The schedule's nominal dates in ``[start, end)``, walked from its start date, up to its end."""
    from app.services.recurring_transaction_service import _advance_date

    found: list[date] = []
    current = rec.start_date
    day = intended_day(rec)
    for _ in range(100_000):
        if current >= end or (rec.end_date and current > rec.end_date):
            break
        if current >= start:
            found.append(current)
            if len(found) >= limit:
                break
        current = _advance_date(current, rec.frequency, intended_day=day)
    return found


def is_occurrence(rec: RecurringTransaction, nominal: date) -> bool:
    return nominal_dates(rec, nominal, nominal + timedelta(days=1)) == [nominal]


# --- reading ------------------------------------------------------------------------------------


async def lock(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID
) -> RecurringTransaction:
    """The schedule, row-locked until commit (generation takes the same lock), freshly read."""
    rec = (
        await session.execute(
            select(RecurringTransaction)
            .where(
                RecurringTransaction.id == recurring_id,
                RecurringTransaction.workspace_id == workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if rec is None:
        raise LifecycleNotFound(NOT_FOUND)
    return rec


async def ledger_rows(
    session: AsyncSession, recurring_ids: set[uuid.UUID], start: date, end: date
) -> dict[tuple[uuid.UUID, date], RecurringOccurrence]:
    if not recurring_ids:
        return {}
    rows = (
        await session.execute(
            select(RecurringOccurrence).where(
                RecurringOccurrence.recurring_transaction_id.in_(recurring_ids),
                RecurringOccurrence.occurrence_date >= start,
                RecurringOccurrence.occurrence_date < end,
            )
        )
    ).scalars().all()
    return {(row.recurring_transaction_id, row.occurrence_date): row for row in rows}


async def handled_occurrences(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_ids: set[uuid.UUID],
    range_start: date,
    range_end: date,
) -> set[tuple[uuid.UUID, date]]:
    """``(schedule, effective date)`` of skipped and overridden occurrences in the range.

    A skipped occurrence is in no view; an overridden one is its own row now (maybe on another
    date), so the schedule's projection of it must go. Ledger dates are nominal: the weekend rule
    moves them at most two days, hence the margin.
    """
    if not recurring_ids:
        return set()
    from app.services.recurring_transaction_service import adjust_weekend_date

    result = await session.execute(
        select(
            RecurringOccurrence.recurring_transaction_id,
            RecurringOccurrence.occurrence_date,
            RecurringTransaction.weekend_adjustment,
        )
        .join(
            RecurringTransaction,
            RecurringTransaction.id == RecurringOccurrence.recurring_transaction_id,
        )
        .where(
            RecurringOccurrence.workspace_id == workspace_id,
            RecurringOccurrence.recurring_transaction_id.in_(recurring_ids),
            RecurringOccurrence.state.in_(_HANDLED_ELSEWHERE),
            RecurringOccurrence.occurrence_date >= range_start - timedelta(days=3),
            RecurringOccurrence.occurrence_date < range_end + timedelta(days=3),
        )
    )
    handled = set()
    for rec_id, nominal, weekend in result.all():
        day = adjust_weekend_date(nominal, weekend)
        if range_start <= day < range_end:
            handled.add((rec_id, day))
    return handled


async def skipped_by_schedule(
    session: AsyncSession, workspace_id: uuid.UUID, start: date, end: date
) -> dict[uuid.UUID, set[date]]:
    """Effective dates of skipped occurrences in ``[start, end)`` per schedule (month summary)."""
    from app.services.recurring_transaction_service import adjust_weekend_date

    result = await session.execute(
        select(
            RecurringOccurrence.recurring_transaction_id,
            RecurringOccurrence.occurrence_date,
            RecurringTransaction.weekend_adjustment,
        )
        .join(
            RecurringTransaction,
            RecurringTransaction.id == RecurringOccurrence.recurring_transaction_id,
        )
        .where(
            RecurringOccurrence.workspace_id == workspace_id,
            RecurringOccurrence.state == "skipped",
            RecurringOccurrence.occurrence_date >= start - timedelta(days=3),
            RecurringOccurrence.occurrence_date < end + timedelta(days=3),
        )
    )
    skipped: dict[uuid.UUID, set[date]] = {}
    for rec_id, nominal, weekend in result.all():
        day = adjust_weekend_date(nominal, weekend)
        if start <= day < end:
            skipped.setdefault(rec_id, set()).add(day)
    return skipped


@dataclass
class Occurrence:
    """One occurrence as the screen shows it.

    ``state``: ``recorded`` (a row), ``edited`` (recorded ahead with its own values), ``skipped``,
    ``removed`` (recorded, then its row was deleted: never generated again), ``scheduled`` (still
    to come), ``overdue`` (dated today or earlier, no row: counts nowhere until recorded, Q12),
    ``past`` (behind the schedule from before the ledger existed).
    """

    recurring_id: uuid.UUID
    nominal_date: date
    date: date
    amount: Decimal
    destination_amount: Optional[Decimal]
    state: str
    transaction_id: Optional[uuid.UUID] = None


async def list_occurrences(
    session: AsyncSession,
    rec: RecurringTransaction,
    start: date,
    end: date,
    today: date,
) -> list[Occurrence]:
    dates = nominal_dates(rec, start, end, limit=MAX_LISTED)
    if not dates:
        return []
    ledger = await ledger_rows(session, {rec.id}, dates[0], dates[-1] + timedelta(days=1))
    tx_ids = [row.transaction_id for row in ledger.values() if row.transaction_id]
    rows: dict[uuid.UUID, Transaction] = {}
    if tx_ids:
        found = await session.execute(select(Transaction).where(Transaction.id.in_(tx_ids)))
        rows = {tx.id: tx for tx in found.scalars().all()}
    listed = []
    for nominal in dates:
        on = effective(rec, nominal)
        entry = ledger.get((rec.id, nominal))
        amount, received, tx_id = rec.amount, rec.destination_amount, None
        if entry is None:
            if nominal < rec.next_occurrence:
                state = "past"
            elif on <= today:
                state = "overdue"
            else:
                state = "scheduled"
        elif entry.state == "skipped":
            state = "skipped"
        else:
            tx = rows.get(entry.transaction_id) if entry.transaction_id else None
            if tx is None:
                state = "removed"
            else:
                state = "edited" if entry.state == "overridden" else "recorded"
                on, amount, tx_id = tx.date, tx.amount, tx.id
        listed.append(Occurrence(rec.id, nominal, on, amount, received, state, tx_id))
    return listed


@dataclass
class Overdue:
    schedule: RecurringTransaction
    nominal_date: date
    date: date


async def overdue(session: AsyncSession, workspace_id: uuid.UUID, today: date) -> list[Overdue]:
    """Occurrences dated today or earlier that are not rows (Q12), oldest first.

    Active schedules on an open account only, from each schedule's pointer (what is behind it was
    dealt with), less what the ledger or a linked row on that date already covers.
    """
    from app.services.dashboard_service import _materialized_recurring_occurrences

    schedules = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.workspace_id == workspace_id,
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.deleted_at.is_(None),
                RecurringTransaction.next_occurrence <= today + timedelta(days=2),
                RecurringTransaction.account_id.is_not(None),
            )
        )
    ).scalars().all()
    if not schedules:
        return []
    account_ids = {rec.account_id for rec in schedules} | {
        rec.destination_account_id for rec in schedules if rec.destination_account_id
    }
    accounts = {
        account.id: account
        for account in (
            await session.execute(select(Account).where(Account.id.in_(account_ids)))
        ).scalars().all()
    }
    earliest = min(rec.next_occurrence for rec in schedules)
    ledger = await ledger_rows(session, {rec.id for rec in schedules}, earliest, today + timedelta(days=3))
    materialized = await _materialized_recurring_occurrences(
        session, workspace_id, {rec.id for rec in schedules}, earliest - timedelta(days=3),
        today + timedelta(days=1),
    )
    found: list[Overdue] = []
    for rec in schedules:
        if not usable(rec, accounts.get(rec.account_id)):
            continue
        if is_transfer_schedule(rec) and not usable(rec, accounts.get(rec.destination_account_id)):
            continue
        for nominal in nominal_dates(rec, rec.next_occurrence, today + timedelta(days=3), limit=MAX_LISTED):
            on = effective(rec, nominal)
            if on > today:
                break
            if (rec.id, nominal) in ledger or (rec.id, on) in materialized:
                continue
            found.append(Overdue(rec, nominal, on))
    found.sort(key=lambda item: (item.date, item.schedule.description, str(item.schedule.id)))
    return found


# --- writing ------------------------------------------------------------------------------------


def _check_open(rec: RecurringTransaction, nominal: date) -> None:
    if rec.deleted_at is not None:
        raise LifecycleConflict(DELETED)
    if not is_occurrence(rec, nominal):
        raise ValueError(NOT_AN_OCCURRENCE)


async def _check_unhandled(session: AsyncSession, rec: RecurringTransaction, nominal: date) -> None:
    if (await ledger_rows(session, {rec.id}, nominal, nominal + timedelta(days=1))):
        raise LifecycleConflict(ALREADY_HANDLED)
    if nominal < rec.next_occurrence:
        raise LifecycleConflict(ALREADY_PASSED)


async def _claim(
    session: AsyncSession, rec: RecurringTransaction, nominal: date, state: str,
    transaction_id: Optional[uuid.UUID] = None,
) -> RecurringOccurrence:
    try:
        async with session.begin_nested():
            row = RecurringOccurrence(
                workspace_id=rec.workspace_id,
                recurring_transaction_id=rec.id,
                occurrence_date=nominal,
                state=state,
                transaction_id=transaction_id,
            )
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise LifecycleConflict(ALREADY_HANDLED) from exc
    return row


async def advance_past_handled(session: AsyncSession, rec: RecurringTransaction) -> None:
    """Move an active schedule's pointer past occurrences the ledger already covers.

    Bank matching reads the pointer as "the next occurrence a charge may fulfil", so a skipped or
    already recorded date must not sit there. A paused or deleted schedule keeps its pointer.
    """
    from app.fork.recurring_generation import advance

    for _ in range(MAX_LISTED):
        if not rec.is_active or rec.deleted_at is not None:
            return
        if rec.end_date and rec.next_occurrence > rec.end_date:
            rec.is_active = False
            return
        at = rec.next_occurrence
        if not await ledger_rows(session, {rec.id}, at, at + timedelta(days=1)):
            return
        advance(rec)


async def skip(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, nominal: date
) -> RecurringOccurrence:
    rec = await lock(session, workspace_id, recurring_id)
    _check_open(rec, nominal)
    await _check_unhandled(session, rec, nominal)
    row = await _claim(session, rec, nominal, "skipped")
    await advance_past_handled(session, rec)
    await session.commit()
    return row


async def unskip(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, nominal: date
) -> RecurringTransaction:
    """Undo a skip. When the pointer had passed the date, it goes back to it, so the occurrence is
    due again; everything between it and the old pointer is in the ledger, so nothing repeats."""
    rec = await lock(session, workspace_id, recurring_id)
    if rec.deleted_at is not None:
        raise LifecycleConflict(DELETED)
    row = (await ledger_rows(session, {rec.id}, nominal, nominal + timedelta(days=1))).get((rec.id, nominal))
    if row is None or row.state != "skipped":
        raise LifecycleConflict(NOT_SKIPPED)
    await session.delete(row)
    if nominal < rec.next_occurrence:
        completed = rec.end_date is not None and rec.next_occurrence > rec.end_date
        rec.next_occurrence = nominal
        if completed and not rec.is_active:
            # The skip itself completed the schedule (its last occurrence); a paused one keeps
            # its pointer on skip, so it never reaches this branch.
            rec.is_active = True
    await session.flush()
    await advance_past_handled(session, rec)
    await session.commit()
    return rec


async def record(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_id: uuid.UUID,
    nominal: date,
    *,
    amount: Optional[Decimal] = None,
    on: Optional[date] = None,
    received: Optional[Decimal] = None,
) -> Transaction:
    """Write one occurrence now as a row: as scheduled, or with its own amount or date.

    Same row generation would write on its date (posted on a manual account, pending on a
    bank-connected one; a transfer as its pair). Changed values make the ledger state
    ``overridden``; the schedule itself does not change. Returns the row (the debit leg).
    """
    from app.fork import recurring_generation

    rec = await lock(session, workspace_id, recurring_id)
    _check_open(rec, nominal)
    await _check_unhandled(session, rec, nominal)
    for value in (amount, received):
        if value is not None and value <= 0:
            raise ValueError(POSITIVE)
    scheduled_on = effective(rec, nominal)
    day = on or scheduled_on
    edited = (amount is not None and amount != rec.amount) or day != scheduled_on or (
        received is not None and received != rec.destination_amount
    )
    account = await session.get(Account, rec.account_id) if rec.account_id else None
    if not usable(rec, account):
        raise LifecycleConflict(ACCOUNT_UNUSABLE)
    assert account is not None
    destination = None
    if is_transfer_schedule(rec):
        destination = await session.get(Account, rec.destination_account_id)
        if not usable(rec, destination):
            raise LifecycleConflict(ACCOUNT_UNUSABLE)
        assert destination is not None
        if (
            account.currency != destination.currency
            and amount is not None and amount != rec.amount and received is None
        ):
            raise ValueError(NEEDS_RECEIVED)
    # Upstream's on-demand rate sync commits; it runs before anything is written, then the lock
    # is taken again and the row is stamped from stored rates (as F-004 generation does).
    currencies = [account.currency, rec.currency] + ([destination.currency] if destination else [])
    await recurring_generation._warm_rates(session, rec.user_id, day, *currencies)
    rec = await lock(session, workspace_id, recurring_id)
    _check_open(rec, nominal)
    await _check_unhandled(session, rec, nominal)
    state = "overridden" if edited else "generated"
    claimed = await _claim(session, rec, nominal, state)
    if destination is not None:
        row = await recurring_generation._write_pair(
            session, rec, rec.user_id, day, account, destination, amount=amount, received=received,
        )
    else:
        row = await _write_row(session, rec, account, day, amount if amount is not None else rec.amount)
    claimed.transaction_id = row.id
    await advance_past_handled(session, rec)
    await session.commit()
    await session.refresh(row)
    return row


async def _write_row(
    session: AsyncSession, rec: RecurringTransaction, account: Account, day: date, amount: Decimal
) -> Transaction:
    """One plain occurrence, built as upstream's `generate_pending` builds its placeholder.

    A copy of that inline block (it is not a function upstream): the same status rule, effective
    bill date, split (sized to the amount), stamp and rules, so a row recorded ahead is the row
    generation would write. Rates were warmed by the caller, so the stamp never commits here.
    """
    from app.services import category_line_service
    from app.services.credit_card_service import apply_effective_date
    from app.services.fx_rate_service import stamp_primary_amount
    from app.services.rule_service import apply_rules_to_transaction

    tx = Transaction(
        user_id=rec.user_id,
        workspace_id=rec.workspace_id,
        account_id=rec.account_id,
        category_id=rec.category_id,
        description=rec.description,
        amount=amount,
        currency=rec.currency,
        date=day,
        type=rec.type,
        source="recurring",
        status="pending" if account.connection_id is not None else "posted",
        recurring_transaction_id=rec.id,
    )
    apply_effective_date(tx, account)
    if rec.has_category_lines:
        category_line_service.copy_lines_to(tx, rec.category_lines)
    session.add(tx)
    await session.flush()
    await stamp_primary_amount(session, rec.user_id, tx, allow_fetch=False)
    await apply_rules_to_transaction(session, rec.user_id, tx)
    if tx.status == "pending":
        tx.is_ignored = False
    return tx


async def link(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_id: uuid.UUID,
    nominal: date,
    transaction_id: uuid.UUID,
) -> Transaction:
    """Make an existing row this occurrence (what generation does when it finds the charge)."""
    from app.services import category_line_service

    rec = await lock(session, workspace_id, recurring_id)
    _check_open(rec, nominal)
    if is_transfer_schedule(rec):
        raise ValueError(TRANSFER_LINK)
    await _check_unhandled(session, rec, nominal)
    tx = (
        await session.execute(
            select(Transaction)
            .where(Transaction.id == transaction_id, Transaction.workspace_id == workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if tx is None:
        raise LifecycleNotFound("Transaction not found")
    if (
        tx.account_id != rec.account_id
        or tx.type != rec.type
        or tx.recurring_transaction_id is not None
        or tx.transfer_pair_id is not None
    ):
        raise ValueError(LINK_MISMATCH)
    tx.recurring_transaction_id = rec.id
    await _claim(session, rec, nominal, "generated", tx.id)
    await category_line_service.apply_template_split(session, tx, rec)
    await advance_past_handled(session, rec)
    await session.commit()
    await session.refresh(tx)
    return tx


async def link_candidates(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, nominal: date, days: int = 20
) -> list[Transaction]:
    """Unlinked rows that could be this occurrence: same account and direction, near its date."""
    rec = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.id == recurring_id,
                RecurringTransaction.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if rec is None:
        raise LifecycleNotFound(NOT_FOUND)
    if is_transfer_schedule(rec) or rec.account_id is None:
        return []
    on = effective(rec, nominal)
    rows = (
        await session.execute(
            select(Transaction).where(
                and_(
                    Transaction.workspace_id == workspace_id,
                    Transaction.account_id == rec.account_id,
                    Transaction.type == rec.type,
                    Transaction.recurring_transaction_id.is_(None),
                    Transaction.transfer_pair_id.is_(None),
                    Transaction.date >= on - timedelta(days=days),
                    Transaction.date <= on + timedelta(days=days),
                )
            )
        )
    ).scalars().all()
    return sorted(rows, key=lambda tx: (abs(tx.amount - rec.amount), abs((tx.date - on).days)))
