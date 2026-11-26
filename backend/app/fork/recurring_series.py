"""Whole-schedule lifecycle (F-005, issue #7): change from a date on, end, delete, restore.

A series is a chain of schedules: "from this date on" ends the current one the day before and starts
a successor (`predecessor_id` points back), so history keeps the old values. Delete, restore and
delete permanently act on the whole chain; change, end and pause act on its current part.

Rows the schedule already wrote are sorted the same way by every action (`impact`):

- history: posted and dated today or earlier; never changed;
- scheduled: pending, or dated after today; they follow the change (new values, or removed when
  the change moves the dates and the row is still to come) or are deleted on request;
- edited: recorded ahead with its own values (ledger ``overridden``); keeps them.

Nothing here runs without an explicit request; every route has a preview that changes nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.models import RecurringOccurrence
from app.fork.recurring_lifecycle import (
    DELETED,
    LifecycleConflict,
    LifecycleNotFound,
    advance_past_handled,
    effective,
    intended_day,
    lock,
    nominal_dates,
)
from app.fork.recurring_series_rows import (
    Impact,
    classify,
    current_day,
    delete_units,
    follow_change,
    follow_edited,
    ledger_by_tx,
)
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate

# Fields whose change moves the rows' dates; only the first two move the nominal dates (skips,
# the ledger and the split's first occurrence are nominal).
DATE_FIELDS = ("frequency", "day_of_month", "weekend_adjustment")
NOMINAL_FIELDS = ("frequency", "day_of_month")
# Fields a split may change (the rest belongs to the series: start, end, activity).
CHANGEABLE = (
    "description", "amount", "currency", "type", "account_id", "destination_account_id",
    "destination_amount", "category_id", "category_lines", "auto_generate", *DATE_FIELDS,
)

FROM_TOO_EARLY = "The change date must be after the series' first occurrence; use \"Correct the whole series\""
FROM_AFTER_END = "The change date is after the series' end"
NOTHING_TO_CHANGE = "Nothing to change"
NOT_THE_CURRENT_PART = "This part of the series has ended; change its current part"
NOT_DELETED = "Only a deleted recurring transaction can be deleted permanently or restored"
END_BEFORE_START = "The last occurrence cannot be before the series starts; delete it instead"
END_BEFORE_PART = "The last occurrence cannot be before {date}, when the current values started"
DUPLICATE_SPLIT = "The series already changes on that date"
NO_OCCURRENCE_LEFT = "The series has no occurrence left from that date on"


def becomes_other_shape(rec: RecurringTransaction, changes: dict[str, Any]) -> bool:
    """A plain schedule turning into a transfer, or back: its rows cannot follow."""
    if "destination_account_id" not in changes:
        return False
    return (changes["destination_account_id"] is None) != (rec.destination_account_id is None)


def first_on_rule(frequency: str, day: int, from_date: date) -> date:
    """The first date on or after ``from_date`` under a new rule (weekly ones start that day)."""
    from app.services.recurring_transaction_service import _advance_months

    if frequency in ("weekly", "biweekly"):
        return from_date
    candidate = _advance_months(from_date.replace(day=1), 0, day)
    return candidate if candidate >= from_date else _advance_months(from_date.replace(day=1), 1, day)


def pointer_floor(rec: RecurringTransaction, impact: Impact, pointer: date, today: date) -> date:
    """Where the pointer may go back to once rows to come were removed: their occurrences are
    due again, but never at or before an occurrence kept as history."""
    floor = min(pointer, today + timedelta(days=1))
    if impact.first_removed is not None:
        floor = min(floor, impact.first_removed)
    if impact.last_history is not None:
        floor = max(floor, impact.last_history + timedelta(days=1))
    return max(floor, rec.start_date)


def move_pointer_back(rec: RecurringTransaction, floor: date) -> None:
    upcoming = nominal_dates(rec, floor, floor + timedelta(days=800), limit=1)
    if upcoming and upcoming[0] < rec.next_occurrence:
        rec.next_occurrence = upcoming[0]


async def chain(session: AsyncSession, rec: RecurringTransaction) -> list[RecurringTransaction]:
    """Every part of rec's series, oldest first, rows locked (one series is small)."""
    parts = {rec.id: rec}
    frontier = {rec.id}
    while frontier:
        found = (
            await session.execute(
                select(RecurringTransaction)
                .where(
                    RecurringTransaction.workspace_id == rec.workspace_id,
                    or_(
                        RecurringTransaction.predecessor_id.in_(frontier),
                        RecurringTransaction.id.in_(
                            [p.predecessor_id for p in parts.values() if p.id in frontier and p.predecessor_id]
                        ),
                    ),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
        frontier = {part.id for part in found} - set(parts)
        for part in found:
            parts.setdefault(part.id, part)
    ordered = sorted(parts.values(), key=lambda part: (part.start_date, part.created_at or datetime.min))
    return ordered


def current_part(parts: list[RecurringTransaction]) -> RecurringTransaction:
    """The part no other part continues."""
    continued = {part.predecessor_id for part in parts if part.predecessor_id}
    heads = [part for part in parts if part.id not in continued]
    return heads[-1]


def _successor_payload(
    rec: RecurringTransaction, changes: dict[str, Any], from_date: date
) -> RecurringTransactionCreate:
    from app.schemas.transaction_category_line import TransactionCategoryLinesInput

    frequency = changes.get("frequency", rec.frequency)
    weekly = frequency in ("weekly", "biweekly")
    if weekly:
        day_of_month = changes.get("day_of_month", rec.day_of_month)
    else:
        # Keep the intended day (a 31st stays a 31st after a short month).
        kept = intended_day(rec) if rec.frequency not in ("weekly", "biweekly") else from_date.day
        day_of_month = changes.get("day_of_month") or kept
    if any(key in changes and changes[key] != getattr(rec, key) for key in NOMINAL_FIELDS):
        start = first_on_rule(frequency, day_of_month or from_date.day, from_date)
        if rec.end_date and start > rec.end_date:
            raise ValueError(NO_OCCURRENCE_LEFT)
    else:
        upcoming = nominal_dates(rec, from_date, from_date + timedelta(days=400), limit=1)
        if not upcoming:
            raise ValueError(NO_OCCURRENCE_LEFT)
        start = upcoming[0]
    lines = changes.get("category_lines")
    if lines is None and rec.has_category_lines and rec.category_lines and "category_id" not in changes:
        from app.services import category_line_service

        ordered = sorted(rec.category_lines, key=lambda line: line.position)
        percent = ordered[0].share_type == "percent"
        amounts = [line.amount for line in ordered]
        new_amount = changes.get("amount", rec.amount)
        if not percent and new_amount != rec.amount:
            # The split keeps its proportions at the new amount, as upstream's edit rescales it.
            amounts = category_line_service._distribute(
                category_line_service._quantize(abs(new_amount)), category_line_service._weights(ordered)
            )
        lines = TransactionCategoryLinesInput.model_validate({
            "share_type": "percent" if percent else "exact",
            "lines": [
                {"category_id": line.category_id, "notes": line.notes,
                 **({"share_pct": line.share_pct} if percent else {"amount": amount})}
                for line, amount in zip(ordered, amounts)
            ],
        })
    values = {
        key: changes.get(key, getattr(rec, key))
        for key in (
            "description", "amount", "currency", "type", "frequency", "weekend_adjustment",
            "account_id", "destination_account_id", "destination_amount", "category_id",
            "auto_generate",
        )
    }
    if values["destination_account_id"] is not None and lines is not None:
        lines = None
    return RecurringTransactionCreate(
        **values,
        day_of_month=day_of_month,
        start_date=start,
        end_date=rec.end_date,
        category_lines=lines,
    )


async def _validate_successor(
    session: AsyncSession, rec: RecurringTransaction, data: RecurringTransactionCreate
) -> None:
    """Upstream's create checks, without its commit (a split is one transaction).

    Upstream's `create_recurring_transaction` commits; a split must end the old part and start the
    new one together, so its checks are repeated here and the successor is written in the session.
    """
    from app.fork import recurring_transfers
    from app.services.recurring_transaction_service import (
        _find_duplicate_recurring,
        _verify_account_in_workspace,
    )

    await _verify_account_in_workspace(session, rec.workspace_id, data.account_id)
    await recurring_transfers.check_create(session, rec.workspace_id, data)
    if data.category_id and data.category_lines and data.category_lines.lines:
        raise ValueError("Choose either a category or a split across categories")
    if await _find_duplicate_recurring(
        session, rec.workspace_id, rec.user_id, data.description, data.frequency, data.start_date
    ) is not None:
        raise LifecycleConflict(DUPLICATE_SPLIT)


async def _create_successor(
    session: AsyncSession, rec: RecurringTransaction, data: RecurringTransactionCreate, *, active: bool
) -> RecurringTransaction:
    from app.services import category_line_service
    from app.services.fx_rate_service import stamp_primary_amount

    successor = RecurringTransaction(
        user_id=rec.user_id,
        workspace_id=rec.workspace_id,
        account_id=data.account_id,
        destination_account_id=data.destination_account_id,
        destination_amount=data.destination_amount,
        category_id=data.category_id,
        description=data.description,
        amount=data.amount,
        currency=data.currency,
        type=data.type,
        frequency=data.frequency,
        weekend_adjustment=data.weekend_adjustment,
        day_of_month=data.day_of_month,
        start_date=data.start_date,
        end_date=data.end_date,
        auto_generate=data.auto_generate,
        is_active=active,
        next_occurrence=data.start_date,
        predecessor_id=rec.id,
    )
    session.add(successor)
    await session.flush()
    await category_line_service.replace_template_lines(session, successor, data.category_lines)
    await stamp_primary_amount(
        session, rec.user_id, successor, date_field="start_date", allow_fetch=False
    )
    return successor


async def _warm(session: AsyncSession, rec: RecurringTransaction, on: date, *currencies: str) -> None:
    """Fetch the rates the writes will stamp with, before writing (the fetch commits)."""
    from app.fork.recurring_generation import _warm_rates
    from app.models.account import Account

    ids = [rec.account_id, rec.destination_account_id]
    found = (await session.execute(select(Account.currency).where(Account.id.in_([i for i in ids if i])))).scalars().all()
    await _warm_rates(session, rec.user_id, on, *{rec.currency, *currencies, *found})


async def _split_plan(
    session: AsyncSession, rec: RecurringTransaction, from_date: date, changes: dict[str, Any]
) -> tuple[Impact, list[list[Transaction]], list[list[Transaction]], list[list[Transaction]], list[RecurringOccurrence]]:
    impact, follow, remove, edited = await classify(
        session, [rec], from_date,
        dates_change=any(key in changes for key in DATE_FIELDS),
        shape_change=becomes_other_shape(rec, changes),
    )
    nominal_move = any(key in changes for key in NOMINAL_FIELDS)
    skipped = list(
        (
            await session.execute(
                select(RecurringOccurrence).where(
                    RecurringOccurrence.recurring_transaction_id == rec.id,
                    RecurringOccurrence.state == "skipped",
                    RecurringOccurrence.occurrence_date >= from_date,
                )
            )
        ).scalars().all()
    )
    days = sorted(row.occurrence_date for row in skipped)
    if nominal_move:
        impact.skipped_dropped = days
    else:
        impact.skipped_kept = days
    return impact, follow, remove, edited, skipped


@dataclass
class ChangeResult:
    schedule: Optional[RecurringTransaction]
    impact: Impact
    preview: bool


async def change_from(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_id: uuid.UUID,
    from_date: date,
    update: RecurringTransactionUpdate,
    *,
    preview: bool,
) -> ChangeResult:
    """Change the series from ``from_date`` on: the current part ends the day before, a successor
    starts with the new values. Earlier occurrences and posted history keep the old values."""
    rec = await lock(session, workspace_id, recurring_id)
    if rec.deleted_at is not None:
        raise LifecycleConflict(DELETED)
    parts = await chain(session, rec)
    if current_part(parts).id != rec.id:
        raise LifecycleConflict(NOT_THE_CURRENT_PART)
    changes = {
        key: value for key, value in update.model_dump(exclude_unset=True).items() if key in CHANGEABLE
    }
    if "category_lines" in changes:
        changes["category_lines"] = update.category_lines
    changes = {
        key: value for key, value in changes.items()
        if key == "category_lines" or value != getattr(rec, key)
    }
    if not changes:
        raise ValueError(NOTHING_TO_CHANGE)
    if from_date <= rec.start_date:
        raise ValueError(FROM_TOO_EARLY)
    if rec.end_date and from_date > rec.end_date:
        raise ValueError(FROM_AFTER_END)
    nominal_move = any(key in changes for key in NOMINAL_FIELDS)
    payload = _successor_payload(rec, changes, from_date)
    impact, *_ = await _split_plan(session, rec, from_date, changes)
    await _validate_successor(session, rec, payload)
    if preview:
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return ChangeResult(None, impact, True)
    await _warm(session, rec, from_date, payload.currency)
    # The warm-up may have committed: lock again and sort the rows as they are now.
    rec = await lock(session, workspace_id, recurring_id)
    impact, follow, remove, edited, skipped = await _split_plan(session, rec, from_date, changes)

    was_active = bool(rec.is_active)
    old_pointer = rec.next_occurrence
    ended = from_date - timedelta(days=1)
    rec.end_date = ended
    if rec.next_occurrence > ended:
        rec.is_active = False
    successor = await _create_successor(session, rec, payload, active=was_active)
    today = current_day(rec)
    floor = max(successor.start_date, pointer_floor(rec, impact, old_pointer, today) if remove else old_pointer)
    upcoming = nominal_dates(successor, floor, floor + timedelta(days=800), limit=1)
    successor.next_occurrence = upcoming[0] if upcoming else floor
    if successor.end_date and successor.next_occurrence > successor.end_date:
        successor.is_active = False
    await delete_units(session, remove)
    await follow_change(session, follow, successor)
    await follow_edited(session, edited, successor)
    if skipped:
        ids = [row.id for row in skipped]
        if nominal_move:
            await session.execute(delete(RecurringOccurrence).where(RecurringOccurrence.id.in_(ids)))
        else:
            await session.execute(
                sql_update(RecurringOccurrence)
                .where(RecurringOccurrence.id.in_(ids))
                .values(recurring_transaction_id=successor.id)
            )
    # Occurrences of the old part already handled at or after the date move with their rows above.
    await session.flush()
    await advance_past_handled(session, successor)
    await session.commit()
    await session.refresh(successor, ["category_lines"])
    await session.refresh(successor)
    return ChangeResult(successor, impact, False)


async def whole_series_impact(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, update: RecurringTransactionUpdate
) -> Impact:
    """What "correct the whole series" does to rows still to come (they follow the new values)."""
    rec = await lock(session, workspace_id, recurring_id)
    changes = update.model_dump(exclude_unset=True)
    dates_change = any(key in changes and changes[key] != getattr(rec, key) for key in DATE_FIELDS)
    impact, *_ = await classify(
        session, [rec], date.min, dates_change=dates_change, shape_change=becomes_other_shape(rec, changes)
    )
    impact.history = 0
    await session.commit()  # nothing was written: ends the transaction, releasing the row lock
    return impact


async def correct_whole_series(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, update: RecurringTransactionUpdate
) -> tuple[RecurringTransaction, Impact]:
    """Upstream's edit of the schedule, then the rows still to come take the new values.

    Two commits: upstream's update validates and commits the schedule; the rows follow in a
    second one under the schedule lock. Posted history is never touched.
    """
    from app.services import recurring_transaction_service

    before = await lock(session, workspace_id, recurring_id)
    if before.deleted_at is not None:
        raise LifecycleConflict(DELETED)
    changes = update.model_dump(exclude_unset=True)
    dates_change = any(key in changes and changes[key] != getattr(before, key) for key in DATE_FIELDS)
    shape_change = becomes_other_shape(before, changes)
    updated = await recurring_transaction_service.update_recurring_transaction(
        session, recurring_id, workspace_id, update
    )
    if updated is None:
        raise LifecycleNotFound("Recurring transaction not found")
    await _warm(session, updated, current_day(updated))
    rec = await lock(session, workspace_id, recurring_id)
    impact, follow, remove, edited = await classify(
        session, [rec], date.min, dates_change=dates_change, shape_change=shape_change
    )
    impact.history = 0
    await delete_units(session, remove)
    await follow_change(session, follow, rec)
    if remove:
        move_pointer_back(rec, pointer_floor(rec, impact, rec.next_occurrence, current_day(rec)))
        await session.flush()
        await advance_past_handled(session, rec)
    await session.commit()
    await session.refresh(rec, ["category_lines"])
    await session.refresh(rec)
    return rec, impact


async def end_series(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_id: uuid.UUID,
    last_date: date,
    *,
    delete_scheduled: bool,
    preview: bool,
) -> tuple[Optional[RecurringTransaction], Impact]:
    """End the series on ``last_date``: nothing after it is generated or projected."""
    rec = await lock(session, workspace_id, recurring_id)
    if rec.deleted_at is not None:
        raise LifecycleConflict(DELETED)
    parts = await chain(session, rec)
    rec = current_part(parts)
    if last_date < parts[0].start_date:
        raise ValueError(END_BEFORE_START)
    if last_date < rec.start_date:
        raise ValueError(END_BEFORE_PART.format(date=rec.start_date.isoformat()))
    after = last_date + timedelta(days=1)
    impact, _, remove, _ = await classify(session, [rec], after, dates_change=False, removing=True)
    if preview:
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return None, impact
    rec.end_date = last_date
    if rec.next_occurrence > last_date:
        rec.is_active = False
    if delete_scheduled:
        await delete_units(session, remove)
    # Skips after the end mean nothing any more.
    await session.execute(
        delete(RecurringOccurrence).where(
            RecurringOccurrence.recurring_transaction_id == rec.id,
            RecurringOccurrence.state == "skipped",
            RecurringOccurrence.occurrence_date > last_date,
        )
    )
    await session.commit()
    await session.refresh(rec)
    return rec, impact


async def trash(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_id: uuid.UUID,
    *,
    delete_scheduled: bool,
    preview: bool,
) -> tuple[list[uuid.UUID], Impact]:
    """Move the series to Deleted: inactive (no generation, projection or bank match), restorable."""
    rec = await lock(session, workspace_id, recurring_id)
    if rec.deleted_at is not None:
        raise LifecycleConflict(DELETED)
    parts = await chain(session, rec)
    impact, _, remove, _ = await classify(
        session, parts, date.min, dates_change=False, removing=True, only_after_today=True
    )
    impact.history = await _count_rows(session, parts)
    if preview:
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return [], impact
    now = datetime.now(timezone.utc)
    for part in parts:
        part.active_before_delete = bool(part.is_active)
        part.is_active = False
        part.deleted_at = now
    if delete_scheduled:
        firsts = _first_removed_by_part(remove, await ledger_by_tx(session, [part.id for part in parts]))
        await delete_units(session, remove)
        # Their occurrences are due again once the series is restored.
        for part in parts:
            if part.id in firsts:
                move_pointer_back(part, max(part.start_date, firsts[part.id]))
    ids = [part.id for part in parts]
    await session.commit()
    return ids, impact


def _first_removed_by_part(
    units: list[list[Transaction]], ledger: dict[uuid.UUID, RecurringOccurrence]
) -> dict[uuid.UUID, date]:
    firsts: dict[uuid.UUID, date] = {}
    for unit in units:
        head = unit[0]
        entry = next((ledger[tx.id] for tx in unit if tx.id in ledger), None)
        day = entry.occurrence_date if entry is not None else head.date
        if head.recurring_transaction_id is not None:
            part = head.recurring_transaction_id
            firsts[part] = min(firsts.get(part, day), day)
    return firsts


async def _count_rows(session: AsyncSession, parts: list[RecurringTransaction]) -> int:
    from sqlalchemy import func

    return int(
        (
            await session.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.workspace_id == parts[0].workspace_id,
                    Transaction.recurring_transaction_id.in_([part.id for part in parts]),
                    or_(Transaction.transfer_pair_id.is_(None), Transaction.type == "debit"),
                )
            )
        ).scalar_one()
    )


@dataclass
class RestorePlan:
    parts: list[uuid.UUID]
    skipped: list[date]
    next_occurrence: Optional[date]


async def restore(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, *, preview: bool
) -> RestorePlan:
    """Bring a deleted series back as it was. Occurrences that fell while it was deleted are
    recorded as skipped, so restoring never writes a backlog."""
    rec = await lock(session, workspace_id, recurring_id)
    if rec.deleted_at is None:
        raise LifecycleConflict(NOT_DELETED)
    parts = await chain(session, rec)
    head = current_part(parts)
    today = current_day(head)
    missed: dict[uuid.UUID, list[date]] = {}
    for part in parts:
        if part.active_before_delete:
            missed[part.id] = await _missed_while_deleted(session, part, today)
    skipped = sorted(day for days in missed.values() for day in days)
    if preview:
        upcoming = nominal_dates(head, today + timedelta(days=1), today + timedelta(days=800), limit=1)
        nxt = upcoming[0] if upcoming and head.active_before_delete else None
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return RestorePlan([], skipped, nxt)
    for part in parts:
        part.is_active = bool(part.active_before_delete)
        part.deleted_at = None
        part.active_before_delete = None
        for nominal in missed.get(part.id, []):
            session.add(
                RecurringOccurrence(
                    workspace_id=part.workspace_id,
                    recurring_transaction_id=part.id,
                    occurrence_date=nominal,
                    state="skipped",
                )
            )
    await session.flush()
    for part in parts:
        await advance_past_handled(session, part)
    plan = RestorePlan([part.id for part in parts], skipped, head.next_occurrence if head.is_active else None)
    await session.commit()
    return plan


async def _missed_while_deleted(session: AsyncSession, part: RecurringTransaction, today: date) -> list[date]:
    """Occurrences of one part that fell between its deletion and today, not in the ledger yet."""
    from app.core.app_clock import app_timezone

    stamp = part.deleted_at
    if stamp is None:
        return []
    if stamp.tzinfo is None:  # SQLite hands timestamps back without their zone
        stamp = stamp.replace(tzinfo=timezone.utc)
    deleted_on = stamp.astimezone(app_timezone()).date()
    days = [
        nominal
        for nominal in nominal_dates(part, max(part.next_occurrence, part.start_date), today + timedelta(days=3))
        if deleted_on < effective(part, nominal) <= today
    ]
    if not days:
        return []
    existing = set(
        (
            await session.execute(
                select(RecurringOccurrence.occurrence_date).where(
                    RecurringOccurrence.recurring_transaction_id == part.id,
                    RecurringOccurrence.occurrence_date.in_(days),
                )
            )
        ).scalars().all()
    )
    return [day for day in days if day not in existing]


async def purge(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, *, preview: bool
) -> tuple[list[uuid.UUID], int]:
    """Delete a deleted series permanently, upstream's way: its rows stay, unlinked."""
    rec = await lock(session, workspace_id, recurring_id)
    if rec.deleted_at is None:
        raise LifecycleConflict(NOT_DELETED)
    parts = await chain(session, rec)
    kept = await _count_rows(session, parts)
    if preview:
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return [], kept
    ids = [part.id for part in parts]
    for part in sorted(parts, key=lambda p: p.start_date, reverse=True):
        await session.delete(part)
    await session.commit()
    return ids, kept



def predecessors_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backup rows of recurring_transactions with each part before its successor.

    A restore inserts them in this order and PostgreSQL checks ``predecessor_id`` row by row;
    an export has no order, and an update can move a predecessor after its successor.
    """
    by_id = {str(row.get("id")): row for row in rows}

    def depth(row: dict[str, Any]) -> int:
        steps, current = 0, row
        while steps < len(rows) and str(current.get("predecessor_id")) in by_id:
            current = by_id[str(current.get("predecessor_id"))]
            steps += 1
        return steps

    return sorted(rows, key=depth)
