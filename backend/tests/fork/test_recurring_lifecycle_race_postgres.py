"""F-005: lifecycle actions racing generation never duplicate or resurrect an occurrence.

Real PostgreSQL, two real sessions that meet at a barrier before their first schedule lock, so both
start from the same stale read: a click on Skip or Save while the hourly job (or a second worker)
runs. Synthetic data.
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.fork import recurring_generation
from app.fork import recurring_lifecycle as lifecycle
from app.fork import recurring_series as series
from app.fork.models import RecurringOccurrence
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionUpdate
from app.services import recurring_transaction_service
from tests.fork._budget_data import TODAY, pin_today
from tests.fork._lifecycle_data import ledger, linked_rows, schedule
from tests.fork._transfer_data import account, transfer_schedule

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


def _gate(monkeypatch, *, generation_locks: bool = True):
    """The first two sessions' first schedule locks wait for each other; later sessions pass."""
    barrier = asyncio.Barrier(2)
    seen: set[int] = set()
    real_generation, real_lifecycle = recurring_generation.lock_schedule, lifecycle.lock

    async def meet(session):
        if len(seen) < 2 and id(session) not in seen:
            seen.add(id(session))
            await barrier.wait()

    async def generation_lock(session, recurring):
        await meet(session)
        if generation_locks:
            return await real_generation(session, recurring)
        return bool(recurring.is_active)

    async def lifecycle_lock(session, workspace_id, recurring_id):
        await meet(session)
        return await real_lifecycle(session, workspace_id, recurring_id)

    monkeypatch.setattr(recurring_generation, "lock_schedule", generation_lock)
    monkeypatch.setattr(lifecycle, "lock", lifecycle_lock)
    monkeypatch.setattr(series, "lock", lifecycle_lock)


def _generate(postgres_sessions, user_id):
    async def run():
        async with postgres_sessions() as run_session:
            return await recurring_transaction_service.generate_pending(run_session, user_id, up_to=TODAY)

    return run()


def _act(postgres_sessions, action):
    async def run():
        async with postgres_sessions() as run_session:
            try:
                return await action(run_session)
            except lifecycle.LifecycleConflict as exc:
                return exc

    return run()


async def _together(*runs):
    return await asyncio.wait_for(asyncio.gather(*runs), timeout=30)


async def _due_today(session, user, ws, *, transfer: bool) -> RecurringTransaction:
    checking = await account(session, user, ws, "Checking")
    if transfer:
        savings = await account(session, user, ws, "Savings", type_="savings")
        rec = await transfer_schedule(session, user, ws, checking, savings, start=TODAY, day=TODAY.day)
    else:
        rec = await schedule(session, user, ws, checking, start=TODAY, amount="90.00", description="Gym")
    await session.commit()
    return rec


@pytest.mark.parametrize("transfer", [False, True], ids=["plain", "transfer"])
async def test_skip_racing_generation_leaves_one_outcome(
    session, postgres_sessions, test_user, test_workspace, monkeypatch, transfer
):
    pin_today(monkeypatch)
    rec = await _due_today(session, test_user, test_workspace, transfer=transfer)
    rec_id, ws_id, user_id = rec.id, test_workspace.id, test_user.id
    _gate(monkeypatch)

    _, skipped = await _together(
        _generate(postgres_sessions, user_id),
        _act(postgres_sessions, lambda s: lifecycle.skip(s, ws_id, rec_id, TODAY)),
    )

    session.expire_all()
    states = await ledger(session, rec_id)
    rows = await linked_rows(session, rec_id)
    assert list(states) == [TODAY]
    if states[TODAY] == "skipped":
        assert rows == [] and not isinstance(skipped, Exception)
    else:
        assert states[TODAY] == "generated" and isinstance(skipped, lifecycle.LifecycleConflict)
        assert len(rows) == (2 if transfer else 1)
    await session.refresh(rec)
    assert rec.next_occurrence > TODAY


@pytest.mark.parametrize("state", ["skipped", "overridden"])
@pytest.mark.parametrize("locked", [True, False], ids=["row-lock", "ledger-only"])
async def test_racing_generators_respect_skipped_and_edited(
    session, postgres_sessions, test_user, test_workspace, monkeypatch, state, locked
):
    """The pointer is still on the handled date (a stale read): neither run may write it again."""
    pin_today(monkeypatch)
    rec = await _due_today(session, test_user, test_workspace, transfer=False)
    edited = None
    if state == "overridden":
        edited = Transaction(
            user_id=test_user.id, workspace_id=test_workspace.id, account_id=rec.account_id,
            recurring_transaction_id=rec.id, description="Gym", amount=Decimal("120.00"), currency="BRL",
            date=TODAY + timedelta(days=2), type="debit", source="recurring", status="posted",
        )
        session.add(edited)
        await session.flush()
    session.add(RecurringOccurrence(
        workspace_id=test_workspace.id, recurring_transaction_id=rec.id, occurrence_date=TODAY, state=state,
        transaction_id=edited.id if edited else None,
    ))
    await session.commit()
    rec_id, user_id, has_edit = rec.id, test_user.id, edited is not None
    _gate(monkeypatch, generation_locks=locked)

    await _together(_generate(postgres_sessions, user_id), _generate(postgres_sessions, user_id))

    session.expire_all()
    assert await ledger(session, rec_id) == {TODAY: state}
    assert [row.amount for row in await linked_rows(session, rec_id)] == ([Decimal("120.00")] if has_edit else [])


async def test_change_from_racing_generation_writes_each_date_once(
    session, postgres_sessions, test_user, test_workspace, monkeypatch
):
    pin_today(monkeypatch)
    checking = await account(session, test_user, test_workspace, "Checking")
    started = date(TODAY.year, TODAY.month - 1, TODAY.day)
    rec = await schedule(
        session, test_user, test_workspace, checking, start=started, pointer=TODAY, amount="90.00",
        description="Gym",
    )
    await session.commit()
    rec_id, ws_id, user_id = rec.id, test_workspace.id, test_user.id
    since = TODAY + timedelta(days=1)
    next_month = date(TODAY.year + TODAY.month // 12, TODAY.month % 12 + 1, TODAY.day)
    _gate(monkeypatch)

    await _together(
        _generate(postgres_sessions, user_id),
        _act(postgres_sessions, lambda s: series.change_from(
            s, ws_id, rec_id, since, RecurringTransactionUpdate(amount=Decimal("110.00")), preview=False,
        )),
    )

    session.expire_all()
    successor = (
        await session.execute(select(RecurringTransaction).where(RecurringTransaction.predecessor_id == rec_id))
    ).scalar_one()
    successor_id = successor.id
    rows = await linked_rows(session, [rec_id, successor_id])
    assert [(row.date, row.amount, row.recurring_transaction_id) for row in rows] == [
        (TODAY, Decimal("90.00"), rec_id)
    ]
    assert successor.next_occurrence == next_month and successor.amount == Decimal("110.00")
    await session.refresh(rec)
    assert rec.end_date == TODAY

    # The next run writes the successor's first date once, from the new amount.
    await recurring_transaction_service.generate_pending(session, user_id, up_to=next_month)
    rows = await linked_rows(session, [rec_id, successor_id])
    assert [(row.date, row.amount) for row in rows] == [(TODAY, Decimal("90.00")), (next_month, Decimal("110.00"))]
