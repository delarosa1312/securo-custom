"""F-005 edge cases found in review: occurrences never repeat or vanish, transfer pairs stay whole."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.fork import recurring_lifecycle as lifecycle
from app.fork import recurring_series as series
from app.fork.recurring_series_rows import Impact
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionUpdate
from app.services import dashboard_service, recurring_match_service
from app.services import recurring_transaction_service as service
from tests.fork._lifecycle_data import ledger, linked_rows, month_day, schedule
from tests.fork._transfer_data import account, transfer_schedule

pytestmark = pytest.mark.asyncio


def _changes(**values) -> RecurringTransactionUpdate:
    return RecurringTransactionUpdate.model_validate(values)


async def _fresh(session, rec_id) -> RecurringTransaction:
    rec = await session.get(RecurringTransaction, rec_id)
    await session.refresh(rec)
    return rec


async def _split(session, ws_id, rec_id, day, changes) -> tuple[RecurringTransaction, Impact]:
    result = await series.change_from(session, ws_id, rec_id, day, changes, preview=False)
    assert result.schedule is not None
    return result.schedule, result.impact


def _charge(user, ws, acc, rec, day, source="import") -> Transaction:
    return Transaction(
        user_id=user.id, workspace_id=ws.id, account_id=acc.id, description="Rent",
        amount=Decimal("1500.00"), currency=acc.currency, date=day, type="debit",
        source=source, status="posted", recurring_transaction_id=rec.id if rec else None,
    )


# --- change from a date: where the new part starts ---------------------------------------------


async def test_new_day_starts_on_that_day(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-2, 5), day=5, pointer=month_day(1, 5))
    await session.commit()
    new, impact = await _split(session, test_workspace.id, rec.id, month_day(2, 1), _changes(day_of_month=20))
    dates = lifecycle.nominal_dates(new, month_day(2, 1), month_day(4, 28))
    assert dates == [month_day(2, 20), month_day(3, 20), month_day(4, 20)]


async def test_weekend_rule_alone_keeps_days_and_skips(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-2, 15), pointer=month_day(1, 15))
    rec.day_of_month = None
    await session.commit()
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(3, 15))
    new, impact = await _split(session, test_workspace.id, rec.id, month_day(2, 1), _changes(weekend_adjustment="next_monday"))
    dates = lifecycle.nominal_dates(new, month_day(2, 1), month_day(4, 28))
    assert dates == [month_day(2, 15), month_day(3, 15), month_day(4, 15)]
    assert (await ledger(session, new.id)).get(month_day(3, 15)) == "skipped"
    assert impact.skipped_dropped == []


async def test_no_occurrence_left_refuses(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(1, 10), end=month_day(3, 5))
    await session.commit()
    with pytest.raises(ValueError, match="no occurrence left"):
        await series.change_from(session, test_workspace.id, rec.id, month_day(3, 1), _changes(amount="1600.00"), preview=True)
    assert (await _fresh(session, rec.id)).end_date == month_day(3, 5)


async def test_edit_recorded_late_stays_with_its_part(session, test_user, test_workspace):
    """Jan 10 recorded on Feb 2, change from Feb 1: Jan 10 is the old part's, never generated again."""
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(1, 10))
    await session.commit()
    late = await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10), on=month_day(3, 2))
    await series.change_from(session, test_workspace.id, rec.id, month_day(3, 1), _changes(amount="1600.00"), preview=False)
    await service.generate_pending(session, test_user.id, up_to=month_day(2, 28), workspace_id=test_workspace.id)
    rows = {tx.id: tx.date for tx in await linked_rows(session, rec.id)}
    assert sorted(rows.values()) == [month_day(1, 10), month_day(3, 2)]
    assert late.id in rows
    assert (await ledger(session, rec.id))[month_day(2, 10)] == "overridden"


# --- rows removed: their occurrences stay due ---------------------------------------------------


async def test_whole_series_new_dates_keep_removed_occurrence_due(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(1, 10))
    await session.commit()
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))
    updated, impact = await series.correct_whole_series(
        session, test_workspace.id, rec.id, _changes(weekend_adjustment="next_monday")
    )
    assert len(impact.removed) == 1
    listed = await lifecycle.list_occurrences(session, updated, month_day(1, 1), month_day(1, 28), app_today())
    assert [o.state for o in listed] == ["scheduled"]
    assert updated.next_occurrence == month_day(1, 10)


async def test_delete_with_scheduled_rows_then_restore_keeps_occurrence(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(1, 10))
    await session.commit()
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))
    await series.trash(session, test_workspace.id, rec.id, delete_scheduled=True, preview=False)
    await series.restore(session, test_workspace.id, rec.id, preview=False)
    fresh = await _fresh(session, rec.id)
    listed = await lifecycle.list_occurrences(session, fresh, month_day(1, 1), month_day(1, 28), app_today())
    assert [o.state for o in listed] == ["scheduled"]


async def test_restore_skips_missed_dates_on_every_part(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-3, 10), pointer=month_day(-2, 10))
    await session.commit()
    new, impact = await _split(session, test_workspace.id, rec.id, month_day(2, 1), _changes(amount="1600.00"))
    await series.trash(session, test_workspace.id, rec.id, delete_scheduled=False, preview=False)
    for part_id in (rec.id, new.id):
        part = await session.get(RecurringTransaction, part_id)
        part.deleted_at = datetime.combine(month_day(-2, 1), time(12), tzinfo=timezone.utc)
    await session.commit()
    plan = await series.restore(session, test_workspace.id, new.id, preview=False)
    assert plan.skipped[:3] == [month_day(-2, 10), month_day(-1, 10), month_day(0, 10)]
    due = await lifecycle.overdue(session, test_workspace.id, app_today())
    assert [item for item in due if item.schedule.id == rec.id] == []
    assert await service.generate_pending(session, test_user.id, up_to=app_today(), workspace_id=test_workspace.id) == 0


# --- undo a skip after a bank or import match ---------------------------------------------------


async def test_unskip_after_import_match_does_not_repeat_it(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-3, 10), pointer=month_day(-3, 10))
    await session.commit()
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(-3, 10))
    charge = _charge(test_user, test_workspace, checking, rec, month_day(-2, 9))
    session.add(charge)
    fresh = await _fresh(session, rec.id)
    recurring_match_service.advance_past(fresh, charge.date)
    await session.commit()
    await lifecycle.unskip(session, test_workspace.id, rec.id, month_day(-3, 10))
    assert (await ledger(session, rec.id)) == {month_day(-2, 10): "generated"}
    await service.generate_pending(session, test_user.id, up_to=month_day(-2, 20), workspace_id=test_workspace.id)
    september = [tx for tx in await linked_rows(session, rec.id) if month_day(-2, 1) <= tx.date <= month_day(-2, 28)]
    assert [tx.id for tx in september] == [charge.id]
    assert (await _fresh(session, rec.id)).next_occurrence > month_day(-2, 10)


async def test_unskip_refuses_when_a_passed_occurrence_has_no_row(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-3, 10), pointer=month_day(-3, 10))
    await session.commit()
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(-3, 10))
    fresh = await _fresh(session, rec.id)
    fresh.next_occurrence = month_day(-1, 10)  # passed September with nothing linked
    await session.commit()
    with pytest.raises(lifecycle.LifecycleConflict, match="would write them again"):
        await lifecycle.unskip(session, test_workspace.id, rec.id, month_day(-3, 10))
    assert (await ledger(session, rec.id)) == {month_day(-3, 10): "skipped"}


# --- transfers ----------------------------------------------------------------------------------


async def test_record_refuses_amount_received_without_currencies_to_convert(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    transfer = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=month_day(1, 10))
    plain = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    for rec in (transfer, plain):
        with pytest.raises(ValueError, match="only for a transfer between currencies"):
            await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10), received=Decimal("50.00"))
    assert await linked_rows(session, [transfer.id, plain.id]) == []


@pytest.mark.parametrize("split", [False, True])
async def test_transfer_becoming_plain_removes_its_scheduled_pairs(session, test_user, test_workspace, split):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=month_day(-1, 10))
    rec.next_occurrence = month_day(1, 10)
    await session.commit()
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))
    changes = _changes(destination_account_id=None, type="debit")
    if split:
        current, impact = await _split(session, test_workspace.id, rec.id, month_day(1, 1), changes)
    else:
        current, impact = await series.correct_whole_series(session, test_workspace.id, rec.id, changes)
    assert len(impact.removed) == 1 and impact.follow == []
    assert await linked_rows(session, [rec.id, current.id]) == []
    assert current.next_occurrence == month_day(1, 10)


async def test_pair_with_a_posted_leg_is_history(session, test_user, test_workspace, test_connection):
    checking = await account(session, test_user, test_workspace, "Checking", connection_id=test_connection.id)
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=month_day(-2, 10))
    rec.next_occurrence = month_day(-1, 10)
    await session.commit()
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(-1, 10))
    legs = {tx.type: tx.status for tx in await linked_rows(session, rec.id)}
    assert legs == {"debit": "pending", "credit": "posted"}
    new, impact = await _split(session, test_workspace.id, rec.id, month_day(-1, 10), _changes(amount="300.00"))
    assert impact.history == 1 and impact.follow == []
    amounts = sorted(tx.amount for tx in await linked_rows(session, [rec.id, new.id]))
    assert amounts == [Decimal("200.00"), Decimal("200.00")]


# --- forecasts, deleted schedules, ending ------------------------------------------------------


async def test_occurrence_linked_ahead_is_not_projected(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(1, 10))
    paid = _charge(test_user, test_workspace, checking, None, app_today(), source="manual")
    session.add(paid)
    await session.commit()
    await lifecycle.link(session, test_workspace.id, rec.id, month_day(2, 10), paid.id)
    today = app_today()
    listed = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, from_date=today, to_date=today + timedelta(days=200)
    )
    days = [item.date for item in listed if item.recurring_id == str(rec.id)]
    assert month_day(1, 10).isoformat() in days
    assert month_day(2, 10).isoformat() not in days


async def test_an_edit_cannot_bring_back_a_deleted_schedule(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10), pointer=month_day(-1, 10))
    await session.commit()
    user_id, ws_id, rec_id = test_user.id, test_workspace.id, rec.id
    await series.trash(session, ws_id, rec_id, delete_scheduled=False, preview=False)
    with pytest.raises(ValueError, match="restore it first"):
        await service.update_recurring_transaction(session, rec_id, ws_id, _changes(is_active=True))
    await session.rollback()
    assert await service.generate_pending(session, user_id, up_to=app_today(), workspace_id=ws_id) == 0


async def test_end_inside_an_earlier_part_names_the_current_start(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-3, 10), pointer=month_day(1, 10))
    await session.commit()
    new, impact = await _split(session, test_workspace.id, rec.id, month_day(1, 1), _changes(amount="1600.00"))
    with pytest.raises(ValueError, match=f"cannot be before {month_day(1, 10).isoformat()}"):
        await series.end_series(session, test_workspace.id, new.id, month_day(-1, 20), delete_scheduled=False, preview=True)
