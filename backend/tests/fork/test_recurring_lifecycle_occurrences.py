"""F-005: one occurrence at a time (skip, undo, record ahead, link) and the overdue list. Synthetic data."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.app_clock import app_today
from app.fork import recurring_lifecycle as lifecycle
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.services import dashboard_service
from app.services import recurring_transaction_service as service
from tests.fork._lifecycle_data import far, ledger, linked_rows, month_day, other_workspace, schedule
from tests.fork._transfer_data import account, transfer_schedule


async def _projected_dates(session, user, ws, rec_id):
    today = app_today()
    listed = await dashboard_service.get_projected_transactions(
        session, ws.id, user.id, from_date=today - timedelta(days=200), to_date=today + timedelta(days=200)
    )
    return [item.date for item in listed if item.recurring_id == str(rec_id)]


async def _balance_dates(session, ws, rec_id):
    today = app_today()
    projected = await dashboard_service._get_recurring_projections(
        session, ws.id, today - timedelta(days=200), today + timedelta(days=200), include_transfer_like=True,
    )
    accounts = {p["date"].isoformat() for p in projected}
    return sorted(accounts)


# --- skip -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_leaves_every_projection_and_generation(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    skipped = month_day(2, 10)

    await lifecycle.skip(session, test_workspace.id, rec.id, skipped)

    assert await ledger(session, rec.id) == {skipped: "skipped"}
    assert skipped.isoformat() not in await _projected_dates(session, test_user, test_workspace, rec.id)
    assert month_day(3, 10).isoformat() in await _projected_dates(session, test_user, test_workspace, rec.id)
    assert skipped.isoformat() not in await _balance_dates(session, test_workspace, rec.id)
    # Generation passes it: no row, the pointer goes on.
    await service.generate_pending(session, test_user.id, up_to=month_day(3, 11))
    rows = await linked_rows(session, rec.id)
    assert [row.date for row in rows] == [month_day(1, 10), month_day(3, 10)]
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(4, 10)


@pytest.mark.asyncio
async def test_skip_at_the_pointer_moves_it_and_undo_brings_it_back(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()

    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(1, 10))
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(2, 10)

    await lifecycle.unskip(session, test_workspace.id, rec.id, month_day(1, 10))
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(1, 10)
    assert await ledger(session, rec.id) == {}
    assert month_day(1, 10).isoformat() in await _projected_dates(session, test_user, test_workspace, rec.id)


@pytest.mark.asyncio
async def test_skipping_the_last_occurrence_completes_and_undo_reopens(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(
        session, test_user, test_workspace, checking, start=month_day(1, 10), end=month_day(1, 10)
    )
    await session.commit()
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(1, 10))
    await session.refresh(rec)
    assert rec.is_active is False
    await lifecycle.unskip(session, test_workspace.id, rec.id, month_day(1, 10))
    await session.refresh(rec)
    assert rec.is_active is True and rec.next_occurrence == month_day(1, 10)


@pytest.mark.asyncio
async def test_a_paused_schedule_keeps_its_pointer_on_skip(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10), active=False)
    await session.commit()
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(1, 10))
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(1, 10) and rec.is_active is False


@pytest.mark.asyncio
async def test_skip_refuses_what_is_not_an_open_occurrence(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    with pytest.raises(ValueError, match="not an occurrence"):
        await lifecycle.skip(session, test_workspace.id, rec.id, month_day(1, 11))
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(2, 10))
    with pytest.raises(lifecycle.LifecycleConflict, match="already recorded or skipped"):
        await lifecycle.skip(session, test_workspace.id, rec.id, month_day(2, 10))
    with pytest.raises(lifecycle.LifecycleConflict, match="not skipped"):
        await lifecycle.unskip(session, test_workspace.id, rec.id, month_day(3, 10))


@pytest.mark.asyncio
async def test_a_generated_occurrence_cannot_be_skipped(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 10))
    await session.commit()
    await service.generate_pending(session, test_user.id, up_to=month_day(-1, 10))
    with pytest.raises(lifecycle.LifecycleConflict):
        await lifecycle.skip(session, test_workspace.id, rec.id, month_day(-1, 10))


@pytest.mark.asyncio
async def test_other_workspace_cannot_touch_the_schedule(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking)
    other = await other_workspace(session, test_user)
    await session.commit()
    with pytest.raises(lifecycle.LifecycleNotFound):
        await lifecycle.skip(session, other.id, rec.id, rec.start_date)


# --- record ahead ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_as_scheduled_on_a_manual_account(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()

    row = await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))

    assert (row.date, row.amount, row.status, row.source) == (month_day(1, 10), Decimal("1500.00"), "posted", "recurring")
    assert await ledger(session, rec.id) == {month_day(1, 10): "generated"}
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(2, 10)
    assert month_day(1, 10).isoformat() not in await _projected_dates(session, test_user, test_workspace, rec.id)
    await service.generate_pending(session, test_user.id, up_to=month_day(1, 11))
    assert len(await linked_rows(session, rec.id)) == 1


@pytest.mark.asyncio
async def test_record_an_edited_occurrence(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    nominal, moved = month_day(2, 10), month_day(2, 12)

    row = await lifecycle.record(
        session, test_workspace.id, rec.id, nominal, amount=Decimal("1550.00"), on=moved
    )

    assert (row.date, row.amount) == (moved, Decimal("1550.00"))
    assert await ledger(session, rec.id) == {nominal: "overridden"}
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(1, 10), "an earlier occurrence is still due"
    assert rec.amount == Decimal("1500.00"), "the series does not change"
    dates = await _projected_dates(session, test_user, test_workspace, rec.id)
    assert nominal.isoformat() not in dates and moved.isoformat() not in dates
    assert month_day(3, 10).isoformat() in dates
    # The projected balance counts the row once and not the template's occurrence.
    projected = await dashboard_service._get_recurring_projections(
        session, test_workspace.id, month_day(2, 1), month_day(3, 1), include_transfer_like=True
    )
    assert [p for p in projected if p["account_id"] == checking.id] == []
    await service.generate_pending(session, test_user.id, up_to=month_day(3, 11))
    rows = await linked_rows(session, rec.id)
    assert [(r.date, r.amount) for r in rows] == [
        (month_day(1, 10), Decimal("1500.00")), (moved, Decimal("1550.00")), (month_day(3, 10), Decimal("1500.00")),
    ]


@pytest.mark.asyncio
async def test_record_on_a_bank_connected_account_is_pending(session, test_user, test_workspace):
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, provider="test",
        external_id="synthetic", institution_name="Synthetic bank", credentials={}, status="active",
    )
    session.add(connection)
    await session.flush()
    checking = await account(session, test_user, test_workspace, "Checking", connection_id=connection.id)
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    row = await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_record_a_transfer_occurrence_writes_its_pair(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=month_day(1, 10))
    await session.commit()

    debit = await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10), amount=Decimal("250.00"))

    legs = await linked_rows(session, rec.id)
    assert [(leg.account_id, leg.type, leg.amount) for leg in legs] == [
        (checking.id, "debit", Decimal("250.00")), (savings.id, "credit", Decimal("250.00")),
    ]
    assert debit.id == legs[0].id and legs[0].transfer_pair_id == legs[1].transfer_pair_id
    assert await ledger(session, rec.id) == {month_day(1, 10): "overridden"}


@pytest.mark.asyncio
async def test_a_cross_currency_transfer_needs_the_received_amount(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    broker = await account(session, test_user, test_workspace, "Broker", type_="investment", currency="USD")
    rec = await transfer_schedule(
        session, test_user, test_workspace, checking, broker, start=month_day(1, 10), destination_amount="40.00"
    )
    await session.commit()
    with pytest.raises(ValueError, match="amount received"):
        await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10), amount=Decimal("250.00"))
    await lifecycle.record(
        session, test_workspace.id, rec.id, month_day(1, 10), amount=Decimal("250.00"), received=Decimal("50.00")
    )
    legs = await linked_rows(session, rec.id)
    assert [leg.amount for leg in legs] == [Decimal("250.00"), Decimal("50.00")]


@pytest.mark.asyncio
async def test_record_refuses_a_closed_account(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    checking.is_closed = True
    await session.commit()
    with pytest.raises(lifecycle.LifecycleConflict, match="closed"):
        await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))


# --- overdue (Q12) --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overdue_lists_past_occurrences_that_are_not_rows(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    manual = await schedule(
        session, test_user, test_workspace, checking, start=month_day(-2, 1), auto=False, description="Water bill",
        amount="85.00",
    )
    await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10), description="Future")
    await session.commit()
    today = app_today()

    found = await lifecycle.overdue(session, test_workspace.id, today)

    expected = [d for d in (month_day(-2, 1), month_day(-1, 1), month_day(0, 1)) if d <= today]
    assert [(item.schedule.id, item.date) for item in found] == [(manual.id, d) for d in expected]
    # Q12: such an occurrence is listed nowhere as projected (Period Transactions, Upcoming).
    projected = await _projected_dates(session, test_user, test_workspace, manual.id)
    assert not {d.isoformat() for d in expected} & set(projected)


@pytest.mark.asyncio
async def test_overdue_actions_resolve_each_occurrence(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(
        session, test_user, test_workspace, checking, start=month_day(-3, 1), auto=False, amount="85.00",
        description="Water bill",
    )
    paid = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=checking.id, description="WATER CO",
        amount=Decimal("87.10"), currency="BRL", date=month_day(-2, 2), type="debit", source="manual",
        status="posted",
    )
    session.add(paid)
    await session.commit()
    first, second, third = month_day(-3, 1), month_day(-2, 1), month_day(-1, 1)

    await lifecycle.record(session, test_workspace.id, rec.id, first)
    candidates = await lifecycle.link_candidates(session, test_workspace.id, rec.id, second)
    assert [tx.id for tx in candidates] == [paid.id]
    await lifecycle.link(session, test_workspace.id, rec.id, second, paid.id)
    await lifecycle.skip(session, test_workspace.id, rec.id, third)

    assert await ledger(session, rec.id) == {first: "generated", second: "generated", third: "skipped"}
    remaining = [item.date for item in await lifecycle.overdue(session, test_workspace.id, app_today())]
    assert first not in remaining and second not in remaining and third not in remaining
    await session.refresh(rec)
    assert rec.next_occurrence == month_day(0, 1)
    await session.refresh(paid)
    assert paid.recurring_transaction_id == rec.id


@pytest.mark.asyncio
async def test_link_refuses_another_account_or_a_linked_row(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(-1, 1), auto=False)
    elsewhere = Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id, account_id=savings.id, description="Rent",
        amount=Decimal("1500.00"), currency="BRL", date=month_day(-1, 1), type="debit", source="manual",
        status="posted",
    )
    session.add(elsewhere)
    await session.commit()
    with pytest.raises(ValueError, match="same account"):
        await lifecycle.link(session, test_workspace.id, rec.id, month_day(-1, 1), elsewhere.id)


@pytest.mark.asyncio
async def test_a_transfer_schedule_takes_no_existing_row(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=month_day(-1, 1))
    await session.commit()
    with pytest.raises(ValueError, match="recurring transfer"):
        await lifecycle.link(session, test_workspace.id, rec.id, month_day(-1, 1), rec.id)
    assert await lifecycle.link_candidates(session, test_workspace.id, rec.id, month_day(-1, 1)) == []


# --- listing, month summary ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_occurrence_states(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(1, 10))
    await lifecycle.record(session, test_workspace.id, rec.id, month_day(2, 10), amount=Decimal("1550.00"))
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(3, 10))
    deleted = await lifecycle.record(session, test_workspace.id, rec.id, month_day(4, 10))
    await session.delete(deleted)
    await session.commit()

    listed = await lifecycle.list_occurrences(session, rec, month_day(1, 1), month_day(6, 1), app_today())

    assert [(item.nominal_date, item.state) for item in listed] == [
        (month_day(1, 10), "recorded"), (month_day(2, 10), "edited"), (month_day(3, 10), "skipped"),
        (month_day(4, 10), "removed"), (month_day(5, 10), "scheduled"),
    ]
    assert listed[1].amount == Decimal("1550.00")
    # A removed occurrence is never generated again.
    await service.generate_pending(session, test_user.id, up_to=month_day(4, 11))
    assert month_day(4, 10) not in [row.date for row in await linked_rows(session, rec.id)]


@pytest.mark.asyncio
async def test_month_summary_leaves_a_skipped_occurrence_out(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(
        session, test_user, test_workspace, checking, start=month_day(1, 3), frequency="weekly", amount="100.00",
    )
    await session.commit()
    month = month_day(1, 1)
    before = await service.get_month_summary(session, test_workspace.id, month, "BRL")
    await lifecycle.skip(session, test_workspace.id, rec.id, month_day(1, 10))
    after = await service.get_month_summary(session, test_workspace.id, month, "BRL")
    assert before.expenses - after.expenses == Decimal("100.00")


@pytest.mark.asyncio
async def test_untouched_schedules_project_as_before(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(session, test_user, test_workspace, checking, start=month_day(1, 10))
    await session.commit()
    dates = await _projected_dates(session, test_user, test_workspace, rec.id)
    assert dates[:3] == [month_day(1, 10).isoformat(), month_day(2, 10).isoformat(), month_day(3, 10).isoformat()]
    assert (await session.execute(select(Transaction).where(Transaction.recurring_transaction_id == rec.id))).first() is None
    assert far() > app_today()
