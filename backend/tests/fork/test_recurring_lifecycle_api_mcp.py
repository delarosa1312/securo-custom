"""F-005 over REST and MCP: the same services, previews that write nothing, roles and the status
filter. Synthetic data."""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio

import mcp_server.tools  # noqa: F401
from app.models.recurring_transaction import RecurringTransaction
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.fork._lifecycle_data import ledger, linked_rows, month_day, other_workspace, schedule
from tests.fork._transfer_data import account

TOOLS = (
    "list_recurring_occurrences", "list_overdue_occurrences", "propose_skip_occurrence",
    "propose_record_occurrence", "propose_link_occurrence", "propose_change_recurring_from",
    "propose_end_recurring", "propose_delete_recurring", "propose_restore_recurring",
)


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


def _call(name):
    return REGISTRY[name].handler


async def _rent(session, user, ws, **extra) -> RecurringTransaction:
    checking = await account(session, user, ws, "Checking")
    rec = await schedule(session, user, ws, checking, start=month_day(1, 10), **extra)
    await session.commit()
    return rec


def test_tools_are_registered():
    assert set(TOOLS) <= set(REGISTRY)
    assert all(REGISTRY[name].is_proposal for name in TOOLS if name.startswith("propose_"))


# --- REST ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_occurrences_skip_and_record(client, auth_headers, session, test_user, test_workspace):
    rec = await _rent(session, test_user, test_workspace)
    base = f"/api/recurring-transactions/{rec.id}"
    second, third = month_day(2, 10), month_day(3, 10)

    listed = await client.get(f"{base}/occurrences", headers=auth_headers)
    assert listed.status_code == 200
    assert {item["nominal_date"]: item["state"] for item in listed.json()}[second.isoformat()] == "scheduled"

    assert (await client.post(f"{base}/occurrences/{second}/skip", headers=auth_headers)).status_code == 204
    again = await client.post(f"{base}/occurrences/{second}/skip", headers=auth_headers)
    assert again.status_code == 409
    recorded = await client.post(
        f"{base}/occurrences/{third}/record", headers=auth_headers, json={"amount": "1550.00"}
    )
    assert recorded.status_code == 201, recorded.text
    assert await ledger(session, rec.id) == {second: "skipped", third: "overridden"}

    assert (await client.delete(f"{base}/occurrences/{second}/skip", headers=auth_headers)).status_code == 204
    assert await ledger(session, rec.id) == {third: "overridden"}
    not_one = await client.post(f"{base}/occurrences/{month_day(2, 11)}/skip", headers=auth_headers)
    assert not_one.status_code == 400 and "occurrence" in not_one.text


@pytest.mark.asyncio
async def test_rest_delete_lists_by_status_and_restores(client, auth_headers, session, test_user, test_workspace):
    rec = await _rent(session, test_user, test_workspace)
    base = "/api/recurring-transactions"

    preview = await client.post(f"{base}/{rec.id}/trash", headers=auth_headers, json={"preview": True})
    assert preview.status_code == 200
    assert (await client.get(f"{base}?status=deleted", headers=auth_headers)).json() == []

    assert (await client.post(f"{base}/{rec.id}/trash", headers=auth_headers, json={})).status_code == 200
    ids = lambda response: [item["id"] for item in response.json()]  # noqa: E731
    assert ids(await client.get(base, headers=auth_headers)) == []
    deleted = await client.get(f"{base}?status=deleted", headers=auth_headers)
    assert ids(deleted) == [str(rec.id)] and deleted.json()[0]["deleted_at"]
    assert ids(await client.get(f"{base}?status=all", headers=auth_headers)) == [str(rec.id)]
    assert (await client.get(f"{base}?status=bogus", headers=auth_headers)).status_code == 422

    restored = await client.post(f"{base}/{rec.id}/restore", headers=auth_headers, json={})
    assert restored.status_code == 200
    assert ids(await client.get(f"{base}?status=active", headers=auth_headers)) == [str(rec.id)]
    refused = await client.post(f"{base}/{rec.id}/purge", headers=auth_headers, json={})
    assert refused.status_code == 409


@pytest.mark.asyncio
async def test_rest_other_workspace_is_not_found(client, auth_headers, session, test_user, test_workspace):
    other = await other_workspace(session, test_user)
    rec = await _rent(session, test_user, other)
    base = f"/api/recurring-transactions/{rec.id}"

    assert (await client.get(f"{base}/occurrences", headers=auth_headers)).status_code == 404
    assert (await client.post(f"{base}/occurrences/{month_day(1, 10)}/skip", headers=auth_headers)).status_code == 404
    assert (await client.post(f"{base}/trash", headers=auth_headers, json={})).status_code == 404
    assert await ledger(session, rec.id) == {}


@pytest.mark.asyncio
async def test_rest_viewer_cannot_write(client, viewer_auth_headers, session, test_user, test_workspace):
    rec = await _rent(session, test_user, test_workspace)
    base = f"/api/recurring-transactions/{rec.id}"

    assert (await client.get(f"{base}/occurrences", headers=viewer_auth_headers)).status_code == 200
    for path, body in (
        (f"{base}/occurrences/{month_day(1, 10)}/skip", None),
        (f"{base}/occurrences/{month_day(1, 10)}/record", {}),
        (f"{base}/end", {"last_date": month_day(2, 10).isoformat()}),
        (f"{base}/trash", {}),
    ):
        assert (await client.post(path, headers=viewer_auth_headers, json=body)).status_code == 403
    assert await ledger(session, rec.id) == {}


# --- MCP ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_skip_and_record_preview_then_apply(session, ctx, ext_ctx, test_user, test_workspace):
    rec = await _rent(session, test_user, test_workspace)
    second, third = month_day(2, 10), month_day(3, 10)

    listed = await _call("list_recurring_occurrences")(session=session, ctx=ctx, recurring_id=str(rec.id))
    assert {item["nominal_date"]: item["state"] for item in listed["items"]}[second.isoformat()] == "scheduled"

    # An in-app conversation only previews, even with apply=true.
    preview = await _call("propose_skip_occurrence")(
        session=session, ctx=ctx, recurring_id=str(rec.id), nominal_date=second.isoformat(), apply=True
    )
    assert preview["kind"] == "skip_occurrence" and "applied" not in preview
    assert await ledger(session, rec.id) == {}

    applied = await _call("propose_skip_occurrence")(
        session=session, ctx=ext_ctx, recurring_id=str(rec.id), nominal_date=second.isoformat(), apply=True
    )
    assert applied["applied"] is True
    twice = await _call("propose_skip_occurrence")(
        session=session, ctx=ext_ctx, recurring_id=str(rec.id), nominal_date=second.isoformat()
    )
    assert "error" in twice

    edited = await _call("propose_record_occurrence")(
        session=session, ctx=ext_ctx, recurring_id=str(rec.id), nominal_date=third.isoformat(),
        amount=1550, date=month_day(3, 12).isoformat(), apply=True,
    )
    assert edited["applied"] is True and edited["proposed"]["amount"] == 1550
    rows = await linked_rows(session, rec.id)
    assert [(row.date, row.amount) for row in rows] == [(month_day(3, 12), 1550)]

    undo = await _call("propose_skip_occurrence")(
        session=session, ctx=ext_ctx, recurring_id=str(rec.id), nominal_date=second.isoformat(),
        undo=True, apply=True,
    )
    assert undo["applied"] is True
    assert await ledger(session, rec.id) == {third: "overridden"}


@pytest.mark.asyncio
async def test_mcp_overdue_list(session, ctx, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    rec = await schedule(
        session, test_user, test_workspace, checking, start=month_day(-1, 5), auto=False, description="Water bill"
    )
    await session.commit()

    listed = await _call("list_overdue_occurrences")(session=session, ctx=ctx)
    assert [(item["recurring_id"], item["nominal_date"]) for item in listed["items"]][0] == (
        str(rec.id), month_day(-1, 5).isoformat()
    )


@pytest.mark.asyncio
async def test_mcp_change_from_preview_writes_nothing(session, ctx, ext_ctx, test_user, test_workspace):
    rec = await _rent(session, test_user, test_workspace)
    since = month_day(3, 1)
    args = {"recurring_id": str(rec.id), "from_date": since.isoformat(), "changes": {"amount": 1600}}

    preview = await _call("propose_change_recurring_from")(session=session, ctx=ctx, **args, apply=True)
    assert preview["kind"] == "change_recurring_from" and "applied" not in preview
    assert preview["impact"] is not None
    await session.refresh(rec)
    assert rec.end_date is None

    applied = await _call("propose_change_recurring_from")(session=session, ctx=ext_ctx, **args, apply=True)
    assert applied["applied"] is True
    successor = await session.get(RecurringTransaction, uuid.UUID(applied["id"]))
    assert successor is not None and successor.predecessor_id == rec.id and successor.amount == 1600
    await session.refresh(rec)
    assert rec.end_date == since - timedelta(days=1)

    old_part = await _call("propose_change_recurring_from")(session=session, ctx=ext_ctx, **args)
    assert "error" in old_part


@pytest.mark.asyncio
async def test_mcp_end_delete_restore_purge(session, ctx, ext_ctx, test_user, test_workspace):
    rec = await _rent(session, test_user, test_workspace)
    rid = str(rec.id)

    ended = await _call("propose_end_recurring")(
        session=session, ctx=ext_ctx, recurring_id=rid, last_date=month_day(4, 10).isoformat(), apply=True
    )
    assert ended["applied"] is True
    await session.refresh(rec)
    assert rec.end_date == month_day(4, 10)

    too_early = await _call("propose_delete_recurring")(
        session=session, ctx=ext_ctx, recurring_id=rid, mode="permanent", apply=True
    )
    assert "error" in too_early

    trashed = await _call("propose_delete_recurring")(session=session, ctx=ext_ctx, recurring_id=rid, apply=True)
    assert trashed["applied"] is True
    listed = await _call("list_recurring_transactions")(session=session, ctx=ctx)
    assert rid not in [item["id"] for item in listed["items"]]
    deleted = await _call("list_recurring_transactions")(session=session, ctx=ctx, status="deleted")
    assert [item["id"] for item in deleted["items"]] == [rid] and deleted["items"][0]["deleted_at"]

    preview = await _call("propose_restore_recurring")(session=session, ctx=ctx, recurring_id=rid, apply=True)
    assert preview["kind"] == "restore_recurring" and "applied" not in preview
    restored = await _call("propose_restore_recurring")(session=session, ctx=ext_ctx, recurring_id=rid, apply=True)
    assert restored["applied"] is True
    await session.refresh(rec)
    assert rec.deleted_at is None and rec.is_active is True

    await _call("propose_delete_recurring")(session=session, ctx=ext_ctx, recurring_id=rid, apply=True)
    purged = await _call("propose_delete_recurring")(
        session=session, ctx=ext_ctx, recurring_id=rid, mode="permanent", apply=True
    )
    assert purged["applied"] is True
    session.expunge_all()
    assert await session.get(RecurringTransaction, rec.id) is None


@pytest.mark.asyncio
async def test_mcp_other_workspace_is_not_found(session, ext_ctx, test_user, test_workspace):
    other = await other_workspace(session, test_user)
    rec = await _rent(session, test_user, other)

    for name, extra in (
        ("propose_skip_occurrence", {"nominal_date": month_day(1, 10).isoformat(), "apply": True}),
        ("propose_delete_recurring", {"apply": True}),
        ("list_recurring_occurrences", {}),
    ):
        assert "error" in await _call(name)(session=session, ctx=ext_ctx, recurring_id=str(rec.id), **extra)
    assert await ledger(session, rec.id) == {}
