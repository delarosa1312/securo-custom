"""F-005: the whole series (change from a date, correct, end, delete, restore, delete permanently).
Synthetic data; dates relative to today."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.app_clock import app_today
from app.fork import recurring_lifecycle as lifecycle
from app.fork import recurring_series as series
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services import dashboard_service
from app.services import recurring_transaction_service as service
from tests.fork._lifecycle_data import ledger, linked_rows, month_day, schedule
from tests.fork._transfer_data import account, transfer_schedule


def _changes(**values) -> RecurringTransactionUpdate:
    return RecurringTransactionUpdate.model_validate(values)


async def _posted(session, user, ws, acc, rec, day, amount="1500.00"):
    """A row the schedule generated in the past (history), with its ledger entry."""
    from app.fork.models import RecurringOccurrence

    tx = Transaction(
        user_id=user.id, workspace_id=ws.id, account_id=acc.id, description=rec.description,
        amount=Decimal(amount), currency=acc.currency, date=day, type=rec.type, source="recurring",
        status="posted", recurring_transaction_id=rec.id,
    )
    session.add(tx)
    await session.flush()
    session.add(RecurringOccurrence(
        workspace_id=ws.id, recurring_transaction_id=rec.id, occurrence_date=day, state="generated",
        transaction_id=tx.id,
    ))
    await session.flush()
    return tx


async def _projected(session, user, ws):
    today = app_today()
    listed = await dashboard_service.get_projected_transactions(
        session, ws.id, user.id, from_date=today, to_date=today + timedelta(days=200)
    )
    return [(item.date, Decimal(str(item.amount)), item.recurring_id) for item in listed]


# --- change from a date on ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_from_splits_the_series_and_keeps_history(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-2, 10), pointer=month_day(0, 10))
    old = [await _posted(session, test_user, test_workspace, checking, rec, month_day(m, 10)) for m in (-2, -1)]
    await session.commit()
    # A scheduled row ahead, an edited one, and a skip after the change date.
    scheduled = await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10))
    edited = await lifecycle.record(session, test_workspace.id, rec.id, month_day(3, 10), amount=Decimal("1550.00"))
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(4, 10))
    since = month_day(2, 10)

    preview = await series.change_from(
        session, test_workspace.id, rec.id, since, _changes(amount="1600.00"), preview=True
    )
    assert preview.schedule is None
    assert [row.date for row in preview.impact.follow] == [month_day(2, 10)]
    assert [row.date for row in preview.impact.edited] == [month_day(3, 10)]
    assert preview.impact.skipped_kept == [month_day(4, 10)]
    await session.refresh(rec)
    assert rec.end_date is None, "a preview changes nothing"

    result = await series.change_from(
        session, test_workspace.id, rec.id, since, _changes(amount="1600.00"), preview=False
    )
    new = result.schedule
    await session.refresh(rec)
    assert rec.end_date == since - timedelta(days=1)
    assert (new.predecessor_id, new.start_date, new.amount) == (rec.id, since, Decimal("1600.00"))
    for row in old:
        await session.refresh(row)
        assert (row.amount, row.recurring_transaction_id) == (Decimal("1500.00"), rec.id)
    await session.refresh(scheduled)
    assert (scheduled.amount, scheduled.recurring_transaction_id) == (Decimal("1600.00"), new.id)
    await session.refresh(edited)
    assert (edited.amount, edited.recurring_transaction_id) == (Decimal("1550.00"), new.id)
    assert await ledger(session, new.id) == {
        month_day(2, 10): "generated", month_day(3, 10): "overridden", month_day(4, 10): "skipped",
    }
    # Old part projects up to the day before, the new one from its first free date; never twice.
    projected = await _projected(session, test_user, test_workspace)
    old_dates = [day for day, _, rid in projected if rid == str(rec.id)]
    new_dates = [(day, amount) for day, amount, rid in projected if rid == str(new.id)]
    assert all(day < since.isoformat() for day in old_dates)
    assert new_dates[0] == (month_day(5, 10).isoformat(), Decimal("1600.00"))


@pytest.mark.asyncio
async def test_salary_change_keeps_past_totals(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    salary = await schedule(
        session, test_user, test_workspace, checking, start=month_day(-3, 5), pointer=month_day(1, 5),
        amount="3000.00", type_="credit", description="Salary",
    )
    for m in (-3, -2, -1, 0):
        if month_day(m, 5) <= app_today():
            await _posted(session, test_user, test_workspace, checking, salary, month_day(m, 5), "3000.00")
    await session.commit()
    before = await service.get_month_summary(session, test_workspace.id, month_day(-1, 1), "BRL")

    result = await series.change_from(
        session, test_workspace.id, salary.id, month_day(1, 5), _changes(amount="3300.00"), preview=False
    )

    after = await service.get_month_summary(session, test_workspace.id, month_day(-1, 1), "BRL")
    assert after.income == before.income == Decimal("3000.00")
    later = await service.get_month_summary(session, test_workspace.id, month_day(2, 1), "BRL")
    assert later.income == Decimal("3300.00")
    assert result.schedule.next_occurrence == month_day(1, 5)


@pytest.mark.asyncio
async def test_change_from_with_new_dates_removes_rows_still_to_come(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    ahead = await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10))
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(3, 10))

    result = await series.change_from(
        session, test_workspace.id, rec.id, month_day(2, 1), _changes(day_of_month=20), preview=False
    )

    assert [row.transaction_id for row in result.impact.removed] == [ahead.id]
    assert result.impact.skipped_dropped == [month_day(3, 10)]
    assert (await session.get(Transaction, ahead.id)) is None
    new = result.schedule
    assert (new.start_date, new.day_of_month, new.next_occurrence) == (month_day(2, 1), 20, month_day(2, 1))
    projected = [day for day, _, rid in await _projected(session, test_user, test_workspace) if rid == str(new.id)]
    assert projected[:3] == [month_day(2, 1).isoformat(), month_day(3, 20).isoformat(), month_day(4, 20).isoformat()]


@pytest.mark.asyncio
async def test_weekend_rule_after_a_split(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 1), weekend="next_monday")
    await session.commit()
    result = await series.change_from(
        session, test_workspace.id, rec.id, month_day(3, 1), _changes(amount="99.00"), preview=False
    )
    new = result.schedule
    assert (new.weekend_adjustment, new.day_of_month) == ("next_monday", 1)
    dates = [day for day, _, rid in await _projected(session, test_user, test_workspace) if rid == str(new.id)]
    for iso in dates:
        day = datetime.fromisoformat(iso).date()
        assert day.weekday() < 5


@pytest.mark.asyncio
async def test_change_from_a_transfer_moves_both_legs(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=month_day(1, 10))
    await session.commit()
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10))

    result = await series.change_from(
        session, test_workspace.id, rec.id, month_day(2, 10), _changes(amount="300.00"), preview=False
    )

    legs = await linked_rows(session, result.schedule.id)
    assert [(leg.type, leg.amount) for leg in legs] == [("debit", Decimal("300.00")), ("credit", Decimal("300.00"))]
    assert result.schedule.destination_account_id == savings.id


@pytest.mark.asyncio
async def test_change_from_refuses_bad_dates_and_old_parts(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10), end=month_day(5, 10))
    await session.commit()
    with pytest.raises(ValueError, match="after the series' first occurrence"):
        await series.change_from(session, test_workspace.id, rec.id, month_day(1, 10), _changes(amount="1"), preview=True)
    with pytest.raises(ValueError, match="after the series' end"):
        await series.change_from(session, test_workspace.id, rec.id, month_day(7, 10), _changes(amount="1"), preview=True)
    with pytest.raises(ValueError, match="Nothing to change"):
        await series.change_from(session, test_workspace.id, rec.id, month_day(2, 10), _changes(amount="1500.00"), preview=True)
    await series.change_from(session, test_workspace.id, rec.id, month_day(2, 10), _changes(amount="1600.00"), preview=False)
    with pytest.raises(lifecycle.LifecycleConflict, match="current part"):
        await series.change_from(session, test_workspace.id, rec.id, month_day(1, 20), _changes(amount="1"), preview=True)


@pytest.mark.asyncio
async def test_correct_whole_series_moves_rows_still_to_come_only(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(0, 10))
    past = await _posted(session, test_user, test_workspace, checking, rec, month_day(-1, 10))
    await session.commit()
    ahead = await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10))

    impact = await series.whole_series_impact(session, test_workspace.id, rec.id, _changes(amount="1450.00"))
    assert [row.transaction_id for row in impact.follow] == [ahead.id]
    updated, _ = await series.correct_whole_series(session, test_workspace.id, rec.id, _changes(amount="1450.00"))

    assert updated.amount == Decimal("1450.00") and updated.predecessor_id is None
    await session.refresh(past)
    await session.refresh(ahead)
    assert (past.amount, ahead.amount) == (Decimal("1500.00"), Decimal("1450.00"))


# --- end, delete, restore, delete permanently --------------------------------------------------------


@pytest.mark.asyncio
async def test_end_series(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    ahead = await lifecycle.record(session, test_workspace.id, rec.id, month_day(3, 10))
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(4, 10))

    _, preview = await series.end_series(
        session, test_workspace.id, rec.id, month_day(2, 10), delete_scheduled=True, preview=True
    )
    assert [row.transaction_id for row in preview.removed] == [ahead.id]
    assert await session.get(Transaction, ahead.id) is not None

    ended, _ = await series.end_series(
        session, test_workspace.id, rec.id, month_day(2, 10), delete_scheduled=True, preview=False
    )
    assert ended.end_date == month_day(2, 10)
    assert await session.get(Transaction, ahead.id) is None
    assert await ledger(session, rec.id) == {}
    await service.generate_pending(session, test_user.id, up_to=month_day(6, 11))
    assert [row.date for row in await linked_rows(session, rec.id)] == [month_day(1, 10), month_day(2, 10)]
    await session.refresh(rec)
    assert rec.is_active is False


@pytest.mark.asyncio
async def test_end_keeps_scheduled_rows_unless_asked(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    ahead = await lifecycle.record(session, test_workspace.id, rec.id, month_day(3, 10))
    await series.end_series(session, test_workspace.id, rec.id, month_day(2, 10), delete_scheduled=False, preview=False)
    assert await session.get(Transaction, ahead.id) is not None


@pytest.mark.asyncio
async def test_delete_restore_and_delete_permanently(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(0, 10))
    past = await _posted(session, test_user, test_workspace, checking, rec, month_day(-1, 10))
    await session.commit()
    ahead = await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10))

    ids, impact = await series.trash(session, test_workspace.id, rec.id, delete_scheduled=True, preview=False)

    assert ids == [rec.id] and impact.history == 2 and [r.transaction_id for r in impact.removed] == [ahead.id]
    await session.refresh(rec)
    assert (rec.is_active, rec.active_before_delete) == (False, True) and rec.deleted_at is not None
    assert await service.get_recurring_transactions(session, test_workspace.id) == []
    assert [r.id for r in await service.get_recurring_transactions(session, test_workspace.id, "deleted")] == [rec.id]
    assert [r for r in await _projected(session, test_user, test_workspace) if r[2] == str(rec.id)] == []
    assert await service.generate_pending(session, test_user.id, include_manual=True, up_to=month_day(3, 11)) == 0
    with pytest.raises(lifecycle.LifecycleConflict, match="deleted"):
        await lifecycle.skip(session, test_workspace.id, rec.id, month_day(3, 10))
    await session.refresh(past)
    assert past.recurring_transaction_id == rec.id

    plan = await series.restore(session, test_workspace.id, rec.id, preview=False)
    await session.refresh(rec)
    assert rec.is_active is True and rec.deleted_at is None and plan.skipped == []

    await series.trash(session, test_workspace.id, rec.id, delete_scheduled=False, preview=False)
    _, kept = await series.purge(session, test_workspace.id, rec.id, preview=False)
    assert kept == 1
    assert await session.get(RecurringTransaction, rec.id) is None
    await session.refresh(past)
    assert past.recurring_transaction_id is None


@pytest.mark.asyncio
async def test_restore_skips_what_fell_while_deleted(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(
        session, test_user, test_workspace, checking, start=month_day(-3, 1), pointer=month_day(-3, 1),
        amount="120.00", description="Gym",
    )
    await session.commit()
    await series.trash(session, test_workspace.id, rec.id, delete_scheduled=False, preview=False)
    # It was deleted a while ago.
    rec.deleted_at = datetime.combine(month_day(-3, 1) - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    await session.commit()

    preview = await series.restore(session, test_workspace.id, rec.id, preview=True)
    missed = [d for d in (month_day(-3, 1), month_day(-2, 1), month_day(-1, 1), month_day(0, 1)) if d <= app_today()]
    assert preview.skipped == missed and preview.parts == []
    plan = await series.restore(session, test_workspace.id, rec.id, preview=False)

    assert plan.skipped == missed
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(1, 1)
    assert await service.generate_pending(session, test_user.id) == 0


@pytest.mark.asyncio
async def test_delete_and_restore_act_on_the_whole_series(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    new = (await series.change_from(
        session, test_workspace.id, rec.id, month_day(3, 10), _changes(amount="1600.00"), preview=False
    )).schedule

    ids, _ = await series.trash(session, test_workspace.id, new.id, delete_scheduled=False, preview=False)
    assert set(ids) == {rec.id, new.id}
    await series.restore(session, test_workspace.id, rec.id, preview=False)
    rows = (await session.execute(select(RecurringTransaction).where(RecurringTransaction.workspace_id == test_workspace.id))).scalars().all()
    assert {(r.id, r.is_active, r.deleted_at) for r in rows} == {(rec.id, True, None), (new.id, True, None)}
    with pytest.raises(lifecycle.LifecycleConflict, match="Only a deleted"):
        await series.purge(session, test_workspace.id, new.id, preview=True)


@pytest.mark.asyncio
async def test_a_duplicate_in_deleted_says_where_it_is(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    await series.trash(session, test_workspace.id, rec.id, delete_scheduled=False, preview=False)
    data = RecurringTransactionCreate(
        description="Rent", amount=Decimal("1500.00"), currency="BRL", type="debit", frequency="monthly",
        start_date=month_day(1, 10), account_id=checking.id,
    )
    with pytest.raises(service.RecurringConflictError, match="restore it"):
        await service.create_recurring_transaction(session, test_workspace.id, test_user.id, data)
