"""F-004 over REST and MCP: create, edit, cancel, roles, isolation, and the conversion of existing
individually scheduled transfers (preview changes nothing; apply per series). Synthetic data."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

import mcp_server.tools  # noqa: F401
from app.fork.models import RecurringOccurrence
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services import recurring_transaction_service
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.fork._transfer_data import account, next_month_day, transfer_schedule


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


def _call(name):
    return REGISTRY[name].handler


async def _accounts(session, user, ws):
    checking = await account(session, user, ws, "Checking")
    savings = await account(session, user, ws, "Savings", type_="savings")
    broker = await account(session, user, ws, "Broker", type_="investment", currency="USD", opening="0")
    await session.commit()
    return checking, savings, broker


def _body(source, destination, **extra):
    body = {
        "description": "Savings plan", "amount": "200.00", "currency": source.currency,
        "type": "debit", "frequency": "monthly", "day_of_month": 10,
        "start_date": next_month_day(10).isoformat(), "account_id": str(source.id),
        "destination_account_id": str(destination.id),
    }
    body.update(extra)
    return body


async def _future_pairs(session, user, ws, source, destination, dates, *, amount="300", description="Rent reserve"):
    for day in dates:
        pair = uuid.uuid4()
        for acc, tx_type in ((source, "debit"), (destination, "credit")):
            session.add(Transaction(
                id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=acc.id,
                description=description, amount=Decimal(amount), currency="BRL", date=day,
                effective_date=day, type=tx_type, source="transfer", status="posted",
                transfer_pair_id=pair,
            ))
    await session.commit()


def _monthly(day: int, count: int) -> list[date]:
    first = next_month_day(day)
    dates = [first]
    for _ in range(count - 1):
        last = dates[-1]
        dates.append((last.replace(day=1) + timedelta(days=32)).replace(day=day))
    return dates


# --- REST ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_create_list_update_and_delete(client, auth_headers, session, test_user, test_workspace):
    checking, savings, broker = await _accounts(session, test_user, test_workspace)
    created = await client.post("/api/recurring-transactions", json=_body(checking, savings), headers=auth_headers)
    assert created.status_code == 201, created.text
    rec = created.json()
    assert rec["destination_account_id"] == str(savings.id) and rec["destination_amount"] is None

    listed = (await client.get("/api/recurring-transactions", headers=auth_headers)).json()
    assert [r["destination_account_id"] for r in listed] == [str(savings.id)]

    refused = await client.patch(
        f"/api/recurring-transactions/{rec['id']}",
        json={"destination_account_id": str(broker.id)}, headers=auth_headers,
    )
    assert refused.status_code == 400
    assert "amount received in the destination account" in refused.json()["detail"]
    moved = await client.patch(
        f"/api/recurring-transactions/{rec['id']}",
        json={"destination_account_id": str(broker.id), "destination_amount": "36.00"},
        headers=auth_headers,
    )
    assert moved.status_code == 200 and moved.json()["destination_amount"] == "36.00"
    paused = await client.patch(
        f"/api/recurring-transactions/{rec['id']}", json={"is_active": False}, headers=auth_headers
    )
    assert paused.json()["is_active"] is False
    deleted = await client.delete(f"/api/recurring-transactions/{rec['id']}", headers=auth_headers)
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_rest_refusals(client, auth_headers, session, test_user, test_workspace):
    checking, savings, broker = await _accounts(session, test_user, test_workspace)
    other = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    foreign = await account(session, test_user, other, "Elsewhere")
    await session.commit()
    for body, detail in [
        (_body(checking, checking), "Cannot transfer to the same account"),
        (_body(checking, broker), "amount received in the destination account"),
        (_body(checking, foreign), "Destination account not found"),
        (_body(checking, savings, type="credit"), "its type is debit"),
    ]:
        response = await client.post("/api/recurring-transactions", json=body, headers=auth_headers)
        assert response.status_code == 400 and detail in response.json()["detail"], response.text


@pytest.mark.asyncio
async def test_viewer_cannot_write(client, viewer_auth_headers, session, test_user, test_workspace):
    checking, savings, _ = await _accounts(session, test_user, test_workspace)
    response = await client.post(
        "/api/recurring-transactions", json=_body(checking, savings), headers=viewer_auth_headers
    )
    assert response.status_code == 403
    preview = await client.get("/api/recurring-transactions/transfer-conversions", headers=viewer_auth_headers)
    assert preview.status_code == 200
    apply = await client.post(
        f"/api/recurring-transactions/transfer-conversions/{uuid.uuid4()}/apply", headers=viewer_auth_headers
    )
    assert apply.status_code == 403


# --- conversion ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversion_preview_changes_nothing_and_apply_links(client, auth_headers, session, test_user, test_workspace):
    checking, savings, _ = await _accounts(session, test_user, test_workspace)
    dates = _monthly(5, 4)
    await _future_pairs(session, test_user, test_workspace, checking, savings, dates)
    # A lone transfer and an irregular pair stay out of any series.
    await _future_pairs(session, test_user, test_workspace, checking, savings, [dates[0]], description="Gift")
    await _future_pairs(
        session, test_user, test_workspace, checking, savings,
        [dates[0], dates[0] + timedelta(days=11)], description="Irregular",
    )
    before = await session.scalar(select(func.count()).select_from(Transaction))

    preview = await client.get("/api/recurring-transactions/transfer-conversions", headers=auth_headers)
    assert preview.status_code == 200
    body = preview.json()
    assert body["left_alone"] == 3
    [series] = body["series"]
    assert (series["frequency"], series["day_of_month"], series["count"]) == ("monthly", 5, 4)
    assert series["first_date"] == dates[0].isoformat() and series["last_date"] == dates[-1].isoformat()
    assert await session.scalar(select(func.count()).select_from(RecurringTransaction)) == 0
    assert await session.scalar(select(func.count()).select_from(Transaction)) == before

    applied = await client.post(
        f"/api/recurring-transactions/transfer-conversions/{series['series_id']}/apply", headers=auth_headers
    )
    assert applied.status_code == 201, applied.text
    result = applied.json()
    assert result["linked_transfers"] == 4
    schedule = result["schedule"]
    assert schedule["end_date"] == dates[-1].isoformat()
    assert schedule["destination_account_id"] == str(savings.id)
    assert await session.scalar(select(func.count()).select_from(Transaction)) == before
    linked = await session.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.recurring_transaction_id == uuid.UUID(schedule["id"])
        )
    )
    assert linked == 8
    assert await session.scalar(select(func.count()).select_from(RecurringOccurrence)) == 4

    # Generation and projections add nothing: the pointer is past the linked transfers.
    assert await recurring_transaction_service.generate_pending(
        session, test_user.id, up_to=dates[-1] + timedelta(days=40)
    ) == 0
    projected = (await client.get(
        "/api/dashboard/projected-transactions",
        params={"from": dates[0].isoformat(), "to": (dates[-1] + timedelta(days=60)).isoformat()},
        headers=auth_headers,
    )).json()
    assert projected == []

    again = await client.post(
        f"/api/recurring-transactions/transfer-conversions/{series['series_id']}/apply", headers=auth_headers
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_conversion_open_ended_and_weekend_rhythm(session, test_user, test_workspace, ext_ctx):
    checking, savings, _ = await _accounts(session, test_user, test_workspace)
    from app.services.recurring_transaction_service import adjust_weekend_date

    nominal = _monthly(15, 6)
    dates = [adjust_weekend_date(d, "next_monday") for d in nominal]
    await _future_pairs(session, test_user, test_workspace, checking, savings, dates)
    listed = await _call("list_transfer_conversions")(session=session, ctx=ext_ctx)
    [series] = listed["series"]
    assert series["day_of_month"] == 15 and series["count"] == 6
    assert series["weekend_adjustment"] == ("none" if dates == nominal else "next_monday")

    preview = await _call("propose_convert_transfer_series")(
        session=session, ctx=ext_ctx, series_id=series["series_id"], open_ended=True
    )
    assert preview["kind"] == "convert_transfer_series" and "applied" not in preview
    assert await session.scalar(select(func.count()).select_from(RecurringTransaction)) == 0
    applied = await _call("propose_convert_transfer_series")(
        session=session, ctx=ext_ctx, series_id=series["series_id"], open_ended=True, apply=True
    )
    assert applied["applied"] is True and applied["linked_transfers"] == 6
    rec = await session.get(RecurringTransaction, uuid.UUID(applied["id"]))
    assert rec is not None and rec.end_date is None


# --- MCP ----------------------------------------------------------------------------------------


def test_tools_are_registered():
    assert REGISTRY["propose_create_recurring_transfer"].is_proposal is True
    assert REGISTRY["propose_update_recurring_transfer"].is_proposal is True
    assert REGISTRY["propose_convert_transfer_series"].is_proposal is True
    assert REGISTRY["list_transfer_conversions"].is_proposal is False


@pytest.mark.asyncio
async def test_mcp_create_preview_and_apply(session, ctx, ext_ctx, test_user, test_workspace):
    checking, savings, broker = await _accounts(session, test_user, test_workspace)
    args = dict(
        description="Savings plan", amount=200, from_account_id=str(checking.id),
        to_account_id=str(savings.id), frequency="monthly", day_of_month=10,
        start_date=next_month_day(10).isoformat(),
    )
    preview = await _call("propose_create_recurring_transfer")(session=session, ctx=ctx, **args, apply=True)
    assert preview["kind"] == "create_recurring_transfer" and "applied" not in preview
    assert preview["proposed"]["to_account_name"] == "Savings"
    assert await session.scalar(select(func.count()).select_from(RecurringTransaction)) == 0

    refused = await _call("propose_create_recurring_transfer")(
        session=session, ctx=ctx, **{**args, "to_account_id": str(broker.id)}
    )
    assert "amount received" in refused["error"]

    applied = await _call("propose_create_recurring_transfer")(session=session, ctx=ext_ctx, **args, apply=True)
    assert applied["applied"] is True
    listed = await _call("list_recurring_transactions")(session=session, ctx=ctx)
    [row] = listed["items"] if "items" in listed else listed["recurring_transactions"]
    assert row["to_account_id"] == str(savings.id) and row["account_id"] == str(checking.id)

    update = await _call("propose_update_recurring_transfer")(
        session=session, ctx=ext_ctx, recurring_id=applied["id"], amount=250, apply=True
    )
    assert update["applied"] is True and update["changes"] == {"amount": 250.0}
    bad = await _call("propose_update_recurring_transfer")(
        session=session, ctx=ext_ctx, recurring_id=applied["id"], to_account_id=str(checking.id)
    )
    assert "same account" in bad["error"]
    cancel = await _call("propose_cancel_recurring_transaction")(
        session=session, ctx=ext_ctx, recurring_id=applied["id"], apply=True
    )
    assert cancel["applied"] is True and cancel["is_active"] is False


@pytest.mark.asyncio
async def test_mcp_update_tool_only_takes_transfers(session, ctx, test_user, test_workspace):
    checking, savings, _ = await _accounts(session, test_user, test_workspace)
    plain = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=checking.id,
        description="Gym", amount=Decimal("90"), currency="BRL", type="debit", frequency="monthly",
        day_of_month=5, start_date=next_month_day(5), next_occurrence=next_month_day(5),
    )
    session.add(plain)
    await transfer_schedule(session, test_user, test_workspace, checking, savings)
    await session.commit()
    result = await _call("propose_update_recurring_transfer")(
        session=session, ctx=ctx, recurring_id=str(plain.id), amount=10
    )
    assert result == {"error": "recurring transfer not found"}


# --- rhythm fitting -----------------------------------------------------------------------------


def test_fit_rhythms():
    from app.fork.transfer_conversion import _fit as fit

    def _fit(dates):
        fitted = fit(dates)
        return fitted[:2] if fitted else None

    # 2027-01-31 (Sunday), month ends: 28 Feb, 31 Mar (Wednesday).
    assert _fit([date(2027, 1, 29), date(2027, 2, 26), date(2027, 3, 31)]) == (
        "monthly", "previous_friday"
    )
    weekly = [date(2027, 3, 3) + timedelta(days=7 * k) for k in range(4)]
    assert _fit(weekly) == ("weekly", "none")
    assert _fit([date(2027, 3, 3), date(2027, 3, 17)]) == ("biweekly", "none")
    assert _fit([date(2027, 1, 10), date(2028, 1, 10)]) == ("yearly", "none")
    assert _fit([date(2027, 1, 10), date(2027, 1, 21)]) is None


@pytest.mark.asyncio
async def test_conversion_apply_refusals(client, auth_headers, session, test_user, test_workspace):
    checking, savings, _ = await _accounts(session, test_user, test_workspace)
    dates = _monthly(5, 3)
    await _future_pairs(session, test_user, test_workspace, checking, savings, dates)
    [series] = (await client.get("/api/recurring-transactions/transfer-conversions", headers=auth_headers)).json()["series"]
    url = f"/api/recurring-transactions/transfer-conversions/{series['series_id']}/apply"
    for body, status in [
        ({"open_ended": True, "end_date": dates[-1].isoformat()}, 400),
        ({"end_date": (dates[-1] - timedelta(days=1)).isoformat()}, 400),
        ({"surprise": 1}, 422),
    ]:
        response = await client.post(url, json=body, headers=auth_headers)
        assert response.status_code == status, response.text
    # Linking one transfer elsewhere makes the previewed series stale.
    row = (await session.execute(
        select(Transaction).where(Transaction.account_id == checking.id, Transaction.date == dates[1])
    )).scalar_one()
    other = await transfer_schedule(session, test_user, test_workspace, checking, savings, description="Other")
    row.recurring_transaction_id = other.id
    await session.commit()
    stale = await client.post(url, headers=auth_headers)
    assert stale.status_code == 409 and "preview again" in stale.json()["detail"]
    assert await session.scalar(select(func.count()).select_from(RecurringTransaction)) == 1
