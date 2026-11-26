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
from dataclasses import dataclass, field
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
    lock,
    nominal_dates,
)
from app.fork.recurring_transfers import is_transfer_schedule
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate

# Fields whose change moves the occurrence dates.
DATE_FIELDS = ("frequency", "day_of_month", "weekend_adjustment")
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
DUPLICATE_SPLIT = "The series already changes on that date"


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


def _today(rec: RecurringTransaction) -> date:
    from app.core.app_clock import app_today

    return app_today()


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


async def _linked_rows(
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


async def _ledger_by_tx(
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


def _units(rows: list[Transaction]) -> list[list[Transaction]]:
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


def _row(unit: list[Transaction]) -> RowImpact:
    head = unit[0]
    return RowImpact(head.id, head.date, head.amount, head.status, head.transfer_pair_id)


def _is_scheduled(tx: Transaction, today: date) -> bool:
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
    today = _today(parts[0])
    ledger = await _ledger_by_tx(session, ids)
    impact = Impact()
    follow: list[list[Transaction]] = []
    remove: list[list[Transaction]] = []
    edited: list[list[Transaction]] = []
    for unit in _units(await _linked_rows(session, ids, workspace_id)):
        head = unit[0]
        entry = next((ledger[tx.id] for tx in unit if tx.id in ledger), None)
        occurrence_day = entry.occurrence_date if entry is not None else head.date
        if occurrence_day < from_date and head.date < from_date:
            continue
        if only_after_today and head.date <= today:
            continue
        if not _is_scheduled(head, today):
            impact.history += 1
            continue
        if removing:
            remove.append(unit)
            impact.removed.append(_row(unit))
        elif entry is not None and entry.state == "overridden" and not (dates_change and head.date > today):
            edited.append(unit)
            impact.edited.append(_row(unit))
        elif dates_change and head.date > today:
            remove.append(unit)
            impact.removed.append(_row(unit))
        else:
            follow.append(unit)
            impact.follow.append(_row(unit))
    return impact, follow, remove, edited


async def _delete_units(session: AsyncSession, units: list[list[Transaction]]) -> None:
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


async def _follow(
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
                await _follow_category(session, tx, target, old_amount)
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


async def _follow_category(
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


def _successor_payload(
    rec: RecurringTransaction, changes: dict[str, Any], from_date: date
) -> RecurringTransactionCreate:
    from app.schemas.transaction_category_line import TransactionCategoryLinesInput

    dates_change = any(key in changes and changes[key] != getattr(rec, key) for key in DATE_FIELDS)
    if dates_change:
        start = from_date
    else:
        upcoming = nominal_dates(rec, from_date, from_date + timedelta(days=400), limit=1)
        start = upcoming[0] if upcoming else from_date
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
    if not dates_change and rec.frequency not in ("weekly", "biweekly"):
        # Keep the intended day (a 31st stays a 31st after a short month).
        day_of_month = rec.day_of_month or rec.start_date.day
    else:
        day_of_month = changes.get("day_of_month", rec.day_of_month)
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
    session: AsyncSession, rec: RecurringTransaction, from_date: date, dates_change: bool
) -> tuple[Impact, list[list[Transaction]], list[list[Transaction]], list[list[Transaction]], list[RecurringOccurrence]]:
    impact, follow, remove, edited = await classify(session, [rec], from_date, dates_change=dates_change)
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
    if dates_change:
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
    dates_change = any(key in changes for key in DATE_FIELDS)
    payload = _successor_payload(rec, changes, from_date)
    impact, *_ = await _split_plan(session, rec, from_date, dates_change)
    await _validate_successor(session, rec, payload)
    if preview:
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return ChangeResult(None, impact, True)
    await _warm(session, rec, from_date, payload.currency)
    # The warm-up may have committed: lock again and sort the rows as they are now.
    rec = await lock(session, workspace_id, recurring_id)
    impact, follow, remove, edited, skipped = await _split_plan(session, rec, from_date, dates_change)

    was_active = bool(rec.is_active)
    old_pointer = rec.next_occurrence
    ended = from_date - timedelta(days=1)
    rec.end_date = ended
    if rec.next_occurrence > ended:
        rec.is_active = False
    successor = await _create_successor(session, rec, payload, active=was_active)
    today = _today(rec)
    floor = max(successor.start_date, old_pointer if not dates_change else min(old_pointer, today + timedelta(days=1)))
    upcoming = nominal_dates(successor, floor, floor + timedelta(days=800), limit=1)
    successor.next_occurrence = upcoming[0] if upcoming else floor
    if successor.end_date and successor.next_occurrence > successor.end_date:
        successor.is_active = False
    await _delete_units(session, remove)
    await _follow(session, follow, successor)
    await _follow_edited(session, edited, successor)
    if skipped:
        ids = [row.id for row in skipped]
        if dates_change:
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


async def _follow_edited(
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


async def whole_series_impact(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID, update: RecurringTransactionUpdate
) -> Impact:
    """What "correct the whole series" does to rows still to come (they follow the new values)."""
    rec = await lock(session, workspace_id, recurring_id)
    changes = update.model_dump(exclude_unset=True)
    dates_change = any(key in changes and changes[key] != getattr(rec, key) for key in DATE_FIELDS)
    impact, *_ = await classify(session, [rec], date.min, dates_change=dates_change)
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
    updated = await recurring_transaction_service.update_recurring_transaction(
        session, recurring_id, workspace_id, update
    )
    if updated is None:
        raise LifecycleNotFound("Recurring transaction not found")
    await _warm(session, updated, _today(updated))
    rec = await lock(session, workspace_id, recurring_id)
    impact, follow, remove, edited = await classify(session, [rec], date.min, dates_change=dates_change)
    impact.history = 0
    await _delete_units(session, remove)
    await _follow(session, follow, rec)
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
    if last_date < rec.start_date:
        raise ValueError(END_BEFORE_START)
    after = last_date + timedelta(days=1)
    impact, _, remove, _ = await classify(session, [rec], after, dates_change=False, removing=True)
    if preview:
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return None, impact
    rec.end_date = last_date
    if rec.next_occurrence > last_date:
        rec.is_active = False
    if delete_scheduled:
        await _delete_units(session, remove)
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
        await _delete_units(session, remove)
    ids = [part.id for part in parts]
    await session.commit()
    return ids, impact


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
    today = _today(head)
    deleted_on = head.deleted_at.date() if head.deleted_at else today
    missed = [
        nominal
        for nominal in nominal_dates(head, max(head.next_occurrence, head.start_date), today + timedelta(days=3))
        if deleted_on < effective(head, nominal) <= today
    ] if head.active_before_delete else []
    existing = {
        row.occurrence_date
        for row in (
            await session.execute(
                select(RecurringOccurrence).where(
                    RecurringOccurrence.recurring_transaction_id == head.id,
                    RecurringOccurrence.occurrence_date.in_(missed),
                )
            )
        ).scalars().all()
    } if missed else set()
    missed = [day for day in missed if day not in existing]
    if preview:
        upcoming = nominal_dates(head, today + timedelta(days=1), today + timedelta(days=800), limit=1)
        nxt = upcoming[0] if upcoming and head.active_before_delete else None
        await session.commit()  # nothing was written: ends the transaction, releasing the row locks
        return RestorePlan([], missed, nxt)
    for part in parts:
        part.is_active = bool(part.active_before_delete)
        part.deleted_at = None
        part.active_before_delete = None
    for nominal in missed:
        session.add(
            RecurringOccurrence(
                workspace_id=head.workspace_id,
                recurring_transaction_id=head.id,
                occurrence_date=nominal,
                state="skipped",
            )
        )
    await session.flush()
    await advance_past_handled(session, head)
    plan = RestorePlan([part.id for part in parts], missed, head.next_occurrence if head.is_active else None)
    await session.commit()
    return plan


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

