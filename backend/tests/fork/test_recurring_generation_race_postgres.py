"""F-004: two generation runs racing over the same due schedules write each occurrence once.

Real PostgreSQL, two real sessions. Both runs read the due schedules and then meet at a barrier
before the first lock, so both start from the same stale pointer: the interleaving of the hourly
job and the Generate button, or two workers. With the row lock the second run waits and then sees
the advanced pointer. Without it (the lock patched out) the unique occurrence key alone keeps
each occurrence single. Synthetic data.
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.fork import recurring_generation
from app.fork.models import RecurringOccurrence
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import recurring_transaction_service
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


async def _schedules(session, user, ws):
    checking = await account(session, user, ws, "Checking")
    savings = await account(session, user, ws, "Savings", type_="savings")
    today = app_today()
    transfer = await transfer_schedule(session, user, ws, checking, savings, start=today, day=today.day)
    plain = RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=checking.id,
        description="Gym", amount=Decimal("90"), currency="BRL", type="debit", frequency="monthly",
        day_of_month=today.day, start_date=today, next_occurrence=today, auto_generate=True,
    )
    session.add(plain)
    await session.commit()
    return transfer, plain


async def _race(postgres_sessions, user_id, monkeypatch, *, locked: bool):
    real_lock = recurring_generation.lock_schedule
    barrier = asyncio.Barrier(2)
    first_call: dict[int, bool] = {}

    async def gated_lock(session, recurring):
        # Only each run's first lock meets the other run: meeting again later would wait on a
        # run that is itself waiting on the row lock.
        if id(session) not in first_call:
            first_call[id(session)] = True
            await barrier.wait()
        if locked:
            return await real_lock(session, recurring)
        return bool(recurring.is_active)

    monkeypatch.setattr(recurring_generation, "lock_schedule", gated_lock)

    async def run():
        async with postgres_sessions() as run_session:
            return await recurring_transaction_service.generate_pending(
                run_session, user_id, up_to=app_today()
            )

    return await asyncio.wait_for(asyncio.gather(run(), run()), timeout=30)


async def _written(session: AsyncSession, transfer, plain):
    legs = (
        await session.execute(
            select(Transaction).where(Transaction.recurring_transaction_id == transfer.id)
        )
    ).scalars().all()
    plain_rows = await session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.recurring_transaction_id == plain.id)
    )
    occurrences = await session.scalar(select(func.count()).select_from(RecurringOccurrence))
    return legs, plain_rows, occurrences


@pytest.mark.parametrize("locked", [True, False], ids=["row-lock", "unique-key-only"])
async def test_racing_runs_write_each_occurrence_once(
    session, postgres_sessions, test_user, test_workspace, monkeypatch, locked
):
    transfer, plain = await _schedules(session, test_user, test_workspace)
    counts = await _race(postgres_sessions, test_user.id, monkeypatch, locked=locked)

    legs, plain_rows, occurrences = await _written(session, transfer, plain)
    assert sorted(leg.type for leg in legs) == ["credit", "debit"]
    assert len({leg.transfer_pair_id for leg in legs}) == 1
    assert plain_rows == 1
    assert occurrences == 2
    assert sorted(counts) == [0, 2]
    for rec in (transfer, plain):
        await session.refresh(rec)
        assert rec.next_occurrence > app_today()


async def test_unique_key_refuses_a_second_occurrence_row(session, test_user, test_workspace):
    transfer, _ = await _schedules(session, test_user, test_workspace)
    for _ in range(2):
        session.add(RecurringOccurrence(
            workspace_id=test_workspace.id, recurring_transaction_id=transfer.id,
            occurrence_date=app_today(), state="generated",
        ))
    with pytest.raises(IntegrityError):
        await session.flush()
