"""F-005 in backups: a split series keeps its link, a deleted schedule stays deleted, and skipped
or individually edited occurrences survive a restore, so nothing is generated again. Synthetic data."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.fork import recurring_lifecycle as lifecycle
from app.fork import recurring_series as series
from app.fork.models import RecurringOccurrence
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionUpdate
from app.services import backup_service
from tests.fork._lifecycle_data import month_day, schedule
from tests.fork._transfer_data import account

pytestmark = pytest.mark.asyncio


async def _seed(session, user, ws):
    checking = await account(session, user, ws, "Checking")
    rent = await schedule(session, user, ws, checking, start=month_day(1, 10), description="Rent")
    gym = await schedule(session, user, ws, checking, start=month_day(1, 5), amount="90.00", description="Gym")
    await session.commit()
    rent_id, gym_id = rent.id, gym.id
    await lifecycle.skip(session, ws.id, rent_id, month_day(2, 10))
    await lifecycle.record(session, ws.id, rent_id, month_day(3, 10), amount=Decimal("1550.00"), on=None, received=None)
    await series.change_from(
        session, ws.id, rent_id, month_day(5, 1), RecurringTransactionUpdate(amount=Decimal("1600.00")), preview=False
    )
    await series.trash(session, ws.id, gym_id, delete_scheduled=False, preview=False)


async def _snapshot(session, ws_id) -> dict:
    """The lifecycle state, keyed by names and dates, not ids."""
    session.expire_all()
    schedules = (
        await session.execute(select(RecurringTransaction).where(RecurringTransaction.workspace_id == ws_id))
    ).scalars().all()
    names = {rec.id: f"{rec.description} {rec.amount}" for rec in schedules}
    rows = {
        tx.id: (tx.date, tx.amount)
        for tx in (await session.execute(select(Transaction).where(Transaction.workspace_id == ws_id))).scalars()
    }
    ledger = (
        await session.execute(select(RecurringOccurrence).where(RecurringOccurrence.workspace_id == ws_id))
    ).scalars().all()
    return {
        "schedules": sorted(
            (
                names[rec.id], names.get(rec.predecessor_id) if rec.predecessor_id else None, rec.end_date,
                rec.next_occurrence, rec.is_active, rec.deleted_at is not None, rec.active_before_delete,
            )
            for rec in schedules
        ),
        "ledger": sorted(
            (names[row.recurring_transaction_id], row.occurrence_date, row.state,
             rows.get(row.transaction_id) if row.transaction_id else None)
            for row in ledger
        ),
    }


async def _restore(session, user_id, ws, content, mode):
    data = await backup_service.build_backup_zip(session, ws, content=content)
    return await backup_service.restore_backup_bytes(
        session, data=data, target_user_id=user_id, current_workspace=ws, content=content, mode=mode,
        confirmation="RESTORE",
    )


@pytest.mark.parametrize(
    ("content", "mode"),
    [
        (backup_service.BackupContent.both, backup_service.BackupRestoreMode.new_workspace),
        (backup_service.BackupContent.both, backup_service.BackupRestoreMode.current_workspace),
        (backup_service.BackupContent.configuration, backup_service.BackupRestoreMode.current_workspace),
    ],
    ids=["both-new", "both-current", "configuration-current"],
)
async def test_lifecycle_survives_a_round_trip(session, test_user, test_workspace, content, mode):
    await _seed(session, test_user, test_workspace)
    ws_id, user_id = test_workspace.id, test_user.id
    before = await _snapshot(session, ws_id)
    assert [item[2] for item in before["ledger"]] == ["skipped", "overridden"]
    assert any(item[1] == "Rent 1500.00" for item in before["schedules"])
    assert any(item[5] for item in before["schedules"])
    await session.refresh(test_workspace)

    result = await _restore(session, user_id, test_workspace, content, mode)

    target = uuid.UUID(str(result.workspace_id)) if mode == backup_service.BackupRestoreMode.new_workspace else ws_id
    assert await _snapshot(session, target) == before


async def test_restored_edit_is_not_generated_again(session, test_user, test_workspace):
    from app.services import recurring_transaction_service

    await _seed(session, test_user, test_workspace)
    user_id = test_user.id
    await session.refresh(test_workspace)
    result = await _restore(
        session, user_id, test_workspace, backup_service.BackupContent.both,
        backup_service.BackupRestoreMode.new_workspace,
    )
    target = uuid.UUID(str(result.workspace_id))

    await recurring_transaction_service.generate_pending(session, user_id, up_to=month_day(4, 11))
    session.expire_all()
    rent = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.workspace_id == target, RecurringTransaction.amount == Decimal("1500.00")
            )
        )
    ).scalar_one()
    rows = (
        await session.execute(select(Transaction).where(Transaction.recurring_transaction_id == rent.id))
    ).scalars().all()
    assert sorted((row.date, row.amount) for row in rows) == [
        (month_day(1, 10), Decimal("1500.00")), (month_day(3, 10), Decimal("1550.00")),
        (month_day(4, 10), Decimal("1500.00")),
    ]
    assert month_day(2, 10) not in {row.date for row in rows}


async def test_successor_listed_before_its_predecessor_restores(session, test_user, test_workspace):
    """An update rewrites the predecessor's row, so PostgreSQL may export it after its successor."""
    import io
    import json
    import zipfile

    from sqlalchemy import update

    await _seed(session, test_user, test_workspace)
    ws_id, user_id = test_workspace.id, test_user.id
    await session.execute(
        update(RecurringTransaction)
        .where(RecurringTransaction.workspace_id == ws_id, RecurringTransaction.predecessor_id.is_(None))
        .values(description=RecurringTransaction.description)
    )
    await session.commit()
    before = await _snapshot(session, ws_id)
    await session.refresh(test_workspace)

    data = await backup_service.build_backup_zip(session, test_workspace)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        listed = json.loads(archive.read("recurring_transactions.json"))
    # Make the order explicit whatever the database returned: successor first.
    listed.sort(key=lambda row: row.get("predecessor_id") is None)
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for item in source.infolist():
            payload = json.dumps(listed) if item.filename == "recurring_transactions.json" else source.read(item)
            target.writestr(item, payload)
    result = await backup_service.restore_backup_bytes(
        session, data=rebuilt.getvalue(), target_user_id=user_id, current_workspace=test_workspace,
        content=backup_service.BackupContent.both, mode=backup_service.BackupRestoreMode.new_workspace,
        confirmation="RESTORE",
    )

    assert await _snapshot(session, uuid.UUID(str(result.workspace_id))) == before
