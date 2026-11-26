"""F-004: transfer schedule validation and generation (pairs, idempotency, dates). Synthetic data."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.fork.models import RecurringOccurrence
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services import recurring_transaction_service as service
from tests.fork._transfer_data import account, transfer_schedule


def _create(source, destination, **overrides) -> RecurringTransactionCreate:
    data = dict(
        description="Savings plan", amount=Decimal("200.00"), currency=source.currency, type="debit",
        frequency="monthly", day_of_month=10, start_date=date(2024, 1, 10),
        account_id=source.id, destination_account_id=destination.id if destination else None,
    )
    data.update(overrides)
    return RecurringTransactionCreate(**data)


async def _rows(session, rec_id=None):
    stmt = select(Transaction).where(Transaction.source == "recurring").order_by(
        Transaction.date, Transaction.type.desc()
    )
    if rec_id is not None:
        stmt = stmt.where(Transaction.recurring_transaction_id == rec_id)
    return list((await session.execute(stmt)).scalars().all())


async def _claims(session, rec_id):
    return list(
        (
            await session.execute(
                select(RecurringOccurrence.occurrence_date)
                .where(RecurringOccurrence.recurring_transaction_id == rec_id)
                .order_by(RecurringOccurrence.occurrence_date)
            )
        ).scalars().all()
    )


# --- validation -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transfer_schedule(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await service.create_recurring_transaction(
        session, test_workspace.id, test_user.id, _create(checking, savings)
    )
    assert rec.destination_account_id == savings.id
    assert rec.destination_amount is None
    assert rec.account_id == checking.id and rec.type == "debit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"type": "credit"}, "type is debit"),
        ({"currency": "USD"}, r"currency is its source account's \(BRL\)"),
        ({"destination_amount": Decimal("10")}, "must be absent for same-currency"),
        ({"skip_first": False, "source_transaction_id": uuid.uuid4()}, "cannot be made from an existing"),
    ],
)
async def test_create_refuses_invalid_transfer(session, test_user, test_workspace, overrides, message):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    with pytest.raises(ValueError, match=message):
        await service.create_recurring_transaction(
            session, test_workspace.id, test_user.id, _create(checking, savings, **overrides)
        )


@pytest.mark.asyncio
async def test_create_refuses_same_account_category_closed_and_foreign_workspace(
    session, test_user, test_workspace, test_categories
):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    closed = await account(session, test_user, test_workspace, "Old", closed=True)
    other_ws = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other_ws)
    await session.flush()
    foreign = await account(session, test_user, other_ws, "Elsewhere")
    cases = [
        (_create(checking, checking), "same account"),
        (_create(checking, savings, category_id=test_categories[0].id), "no category"),
        (_create(checking, closed), "closed account"),
        (_create(checking, foreign), "Destination account not found"),
        (_create(checking, None, destination_amount=Decimal("5")), "needs destination_account_id"),
    ]
    for data, message in cases:
        with pytest.raises(ValueError, match=message):
            await service.create_recurring_transaction(session, test_workspace.id, test_user.id, data)


@pytest.mark.asyncio
async def test_cross_currency_needs_fixed_destination_amount(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    broker = await account(session, test_user, test_workspace, "Broker", type_="investment", currency="USD")
    with pytest.raises(ValueError, match="amount received in the destination account"):
        await service.create_recurring_transaction(
            session, test_workspace.id, test_user.id, _create(checking, broker)
        )
    rec = await service.create_recurring_transaction(
        session, test_workspace.id, test_user.id,
        _create(checking, broker, destination_amount=Decimal("36.00")),
    )
    assert rec.destination_amount == Decimal("36.00")


@pytest.mark.asyncio
async def test_update_rules(session, test_user, test_workspace, test_categories):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    broker = await account(session, test_user, test_workspace, "Broker", type_="investment", currency="USD")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings)
    rec2 = await transfer_schedule(session, test_user, test_workspace, checking, savings, description="Other")
    await session.commit()
    ws_id, rec_id, rec2_id = test_workspace.id, rec.id, rec2.id
    checking_id, savings_id, broker_id = checking.id, savings.id, broker.id
    category_id = test_categories[0].id

    for data, message in [
        (RecurringTransactionUpdate(category_id=category_id), "no category"),
        (RecurringTransactionUpdate(type="credit"), "type is debit"),
        (RecurringTransactionUpdate(destination_account_id=broker_id), "amount received"),
        (RecurringTransactionUpdate(destination_account_id=checking_id), "same account"),
    ]:
        with pytest.raises(ValueError, match=message):
            await service.update_recurring_transaction(session, rec_id, ws_id, data)
        await session.rollback()

    moved = await service.update_recurring_transaction(
        session, rec_id, ws_id,
        RecurringTransactionUpdate(destination_account_id=broker_id, destination_amount=Decimal("36")),
    )
    assert moved.destination_account_id == broker_id and moved.destination_amount == Decimal("36")
    # An explicit null turns it back into a plain schedule and drops the received amount.
    plain = await service.update_recurring_transaction(
        session, rec_id, ws_id, RecurringTransactionUpdate(destination_account_id=None)
    )
    assert plain.destination_account_id is None and plain.destination_amount is None
    # An edit that does not touch the transfer fields keeps them.
    kept = await service.update_recurring_transaction(
        session, rec2_id, ws_id, RecurringTransactionUpdate(amount=Decimal("250"))
    )
    assert kept.destination_account_id == savings_id and kept.amount == Decimal("250")


# --- generation -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_writes_one_linked_pair_per_occurrence(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(
        session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10)
    )
    await session.commit()

    count = await service.generate_pending(session, test_user.id, up_to=date(2024, 3, 15))
    assert count == 3
    rows = await _rows(session, rec.id)
    assert [(r.date, r.type, r.account_id, r.amount) for r in rows] == [
        (date(2024, 1, 10), "debit", checking.id, Decimal("200.00")),
        (date(2024, 1, 10), "credit", savings.id, Decimal("200.00")),
        (date(2024, 2, 10), "debit", checking.id, Decimal("200.00")),
        (date(2024, 2, 10), "credit", savings.id, Decimal("200.00")),
        (date(2024, 3, 10), "debit", checking.id, Decimal("200.00")),
        (date(2024, 3, 10), "credit", savings.id, Decimal("200.00")),
    ]
    for debit, credit in zip(rows[::2], rows[1::2]):
        assert debit.transfer_pair_id is not None and debit.transfer_pair_id == credit.transfer_pair_id
        assert debit.status == credit.status == "posted"
        assert debit.category_id is None and credit.category_id is None
    await session.refresh(rec)
    assert rec.next_occurrence == date(2024, 4, 10)
    assert await _claims(session, rec.id) == [date(2024, 1, 10), date(2024, 2, 10), date(2024, 3, 10)]

    # A second run (the button after the hourly job) writes nothing more.
    assert await service.generate_pending(
        session, test_user.id, up_to=date(2024, 3, 15), include_manual=True
    ) == 0
    assert len(await _rows(session, rec.id)) == 6


@pytest.mark.asyncio
async def test_a_claimed_occurrence_is_never_written_again(session, test_user, test_workspace):
    """The pointer lags behind (a run that wrote and then lost its pointer update): the claim holds."""
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10))
    plain = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=checking.id,
        description="Gym", amount=Decimal("90"), currency="BRL", type="debit", frequency="monthly",
        day_of_month=5, start_date=date(2024, 1, 5), next_occurrence=date(2024, 1, 5),
    )
    session.add(plain)
    for rid, day in ((rec.id, date(2024, 1, 10)), (plain.id, date(2024, 1, 5))):
        session.add(RecurringOccurrence(
            workspace_id=test_workspace.id, recurring_transaction_id=rid, occurrence_date=day,
            state="generated",
        ))
    await session.commit()

    assert await service.generate_pending(session, test_user.id, up_to=date(2024, 2, 20)) == 2
    assert [r.date for r in await _rows(session, rec.id)] == [date(2024, 2, 10)] * 2
    assert [r.date for r in await _rows(session, plain.id)] == [date(2024, 2, 5)]
    claim = (
        await session.execute(
            select(RecurringOccurrence).where(
                RecurringOccurrence.recurring_transaction_id == plain.id,
                RecurringOccurrence.occurrence_date == date(2024, 2, 5),
            )
        )
    ).scalar_one()
    assert claim.transaction_id == (await _rows(session, plain.id))[0].id


@pytest.mark.asyncio
async def test_pair_is_atomic(session, test_user, test_workspace, monkeypatch):
    """The credit leg fails: neither leg nor the claim stays, the pointer waits, others go on."""
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    broken = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10))
    fine = await transfer_schedule(
        session, test_user, test_workspace, savings, checking, start=date(2024, 1, 12),
        description="Back to checking",
    )
    await session.commit()
    broken_id = broken.id

    from app.services import fx_rate_service

    original = fx_rate_service.stamp_primary_amount

    async def failing(session_, user_id, tx, *args, **kwargs):
        if tx.type == "credit" and tx.recurring_transaction_id == broken_id:
            raise RuntimeError("synthetic failure on the second leg")
        return await original(session_, user_id, tx, *args, **kwargs)

    monkeypatch.setattr(fx_rate_service, "stamp_primary_amount", failing)
    assert await service.generate_pending(session, test_user.id, up_to=date(2024, 1, 20)) == 1
    assert await _rows(session, broken.id) == []
    assert await _claims(session, broken.id) == []
    await session.refresh(broken)
    assert broken.next_occurrence == date(2024, 1, 10)
    assert len(await _rows(session, fine.id)) == 2

    monkeypatch.setattr(fx_rate_service, "stamp_primary_amount", original)
    assert await service.generate_pending(session, test_user.id, up_to=date(2024, 1, 20)) == 1
    assert len(await _rows(session, broken.id)) == 2


@pytest.mark.asyncio
async def test_weekend_adjustment_month_end_and_leap_day(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    # 2024-03-10 is a Sunday; previous Friday is the 8th.
    weekend = await transfer_schedule(
        session, test_user, test_workspace, checking, savings, start=date(2024, 3, 10),
        weekend="previous_friday", description="Weekend",
    )
    month_end = await transfer_schedule(
        session, test_user, test_workspace, checking, savings, start=date(2024, 1, 31),
        description="Month end",
    )
    leap = await transfer_schedule(
        session, test_user, test_workspace, checking, savings, start=date(2024, 2, 29),
        frequency="yearly", description="Leap",
    )
    await session.commit()
    await service.generate_pending(session, test_user.id, up_to=date(2025, 3, 31))
    assert [r.date for r in await _rows(session, weekend.id)][:2] == [date(2024, 3, 8)] * 2
    assert [r.date for r in await _rows(session, month_end.id)][:6:2] == [
        date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31),
    ]
    assert [r.date for r in await _rows(session, leap.id)][::2] == [date(2024, 2, 29), date(2025, 2, 28)]


@pytest.mark.asyncio
async def test_end_date_and_deactivated(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    ending = await transfer_schedule(
        session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10),
        end=date(2024, 2, 10), description="Ending",
    )
    paused = await transfer_schedule(
        session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10),
        active=False, description="Paused",
    )
    await session.commit()
    await service.generate_pending(session, test_user.id, up_to=date(2024, 6, 30))
    assert len(await _rows(session, ending.id)) == 4
    await session.refresh(ending)
    assert ending.is_active is False
    assert await _rows(session, paused.id) == []


@pytest.mark.asyncio
async def test_closed_or_moved_destination_waits(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10))
    savings.is_closed = True
    await session.commit()
    assert await service.generate_pending(session, test_user.id, up_to=date(2024, 2, 20)) == 0
    await session.refresh(rec)
    assert rec.next_occurrence == date(2024, 1, 10) and rec.is_active is True

    savings.is_closed = False
    other_ws = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other_ws)
    await session.flush()
    savings.workspace_id = other_ws.id
    await session.commit()
    assert await service.generate_pending(session, test_user.id, up_to=date(2024, 2, 20)) == 0

    savings.workspace_id = test_workspace.id
    await session.commit()
    assert await service.generate_pending(session, test_user.id, up_to=date(2024, 2, 20)) == 2


@pytest.mark.asyncio
async def test_cross_currency_and_connected_legs(session, test_user, test_workspace):
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, provider="test",
        external_id="synthetic", institution_name="Synthetic bank", credentials={}, status="active",
    )
    session.add(connection)
    await session.flush()
    checking = await account(session, test_user, test_workspace, "Checking", connection_id=connection.id)
    broker = await account(session, test_user, test_workspace, "Broker", type_="investment", currency="USD")
    rec = await transfer_schedule(
        session, test_user, test_workspace, checking, broker, amount="200.00",
        destination_amount="36.00", start=date(2024, 1, 10),
    )
    await session.commit()
    await service.generate_pending(session, test_user.id, up_to=date(2024, 1, 31))
    debit, credit = await _rows(session, rec.id)
    assert (debit.amount, debit.currency, debit.status) == (Decimal("200.00"), "BRL", "pending")
    assert (credit.amount, credit.currency, credit.status) == (Decimal("36.00"), "USD", "posted")
    assert debit.transfer_amount_explicit and credit.transfer_amount_explicit
    if debit.amount_primary is not None:
        assert credit.amount_primary == debit.amount_primary


@pytest.mark.asyncio
async def test_month_summary_counts_no_income_or_expense(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    await transfer_schedule(session, test_user, test_workspace, checking, savings, start=date(2024, 1, 10))
    await session.commit()
    summary = await service.get_month_summary(session, test_workspace.id, date(2024, 3, 1), "BRL")
    assert (summary.income, summary.expenses, summary.transfer_count) == (Decimal("0"), Decimal("0"), 1)


@pytest.mark.asyncio
async def test_deleting_the_destination_account_deletes_the_schedule(session, test_user, test_workspace):
    from app.services.account_service import delete_account

    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings)
    await session.commit()
    await delete_account(session, savings.id, test_workspace.id)
    remaining = await session.scalar(
        select(func.count()).select_from(RecurringTransaction).where(RecurringTransaction.id == rec.id)
    )
    assert remaining == 0
