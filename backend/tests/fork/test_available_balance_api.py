"""F-001 over HTTP: the setting on the account, the available-balance route, and
the figures that must not move. Synthetic accounts only."""

import io
import json
import uuid
import zipfile
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

URL = "/api/accounts/available-balance"


async def _create(client: AsyncClient, headers: dict, name: str, type_: str, balance: str) -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": name, "type": type_, "balance": balance, "currency": "BRL"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _worked_example(client: AsyncClient, headers: dict) -> dict[str, dict]:
    return {
        "checking": await _create(client, headers, "Current account", "checking", "500.00"),
        "wallet": await _create(client, headers, "Cash", "wallet", "100.00"),
        "savings": await _create(client, headers, "Reserve", "savings", "1000.00"),
        "card": await _create(client, headers, "Card", "credit_card", "300.00"),
    }


async def _patch(client: AsyncClient, headers: dict, account_id: str, **body):
    return await client.patch(f"/api/accounts/{account_id}", headers=headers, json=body)


@pytest.mark.asyncio
async def test_new_accounts_are_included_by_default(client: AsyncClient, auth_headers):
    created = await _create(client, auth_headers, "Current account", "checking", "500.00")
    assert created["include_in_available"] is True
    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    assert listed[0]["include_in_available"] is True
    one = (await client.get(f"/api/accounts/{created['id']}", headers=auth_headers)).json()
    assert one["include_in_available"] is True


@pytest.mark.asyncio
async def test_worked_example(client: AsyncClient, auth_headers):
    accounts = await _worked_example(client, auth_headers)
    before = (await client.get(URL, headers=auth_headers)).json()
    assert before["total_primary"] == 1600.0
    assert before["excluded_accounts"] == []

    resp = await _patch(client, auth_headers, accounts["savings"]["id"], include_in_available=False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["include_in_available"] is False

    body = (await client.get(URL, headers=auth_headers)).json()
    assert body["primary_currency"] == "BRL"
    assert body["total_primary"] == 600.0
    assert body["by_currency"] == {"BRL": 600.0}
    assert body["fx_fallback"] is False
    assert {a["name"] for a in body["accounts"]} == {"Current account", "Cash"}
    assert [a["name"] for a in body["excluded_accounts"]] == ["Reserve"]


@pytest.mark.asyncio
async def test_collection_scope(client: AsyncClient, auth_headers):
    accounts = await _worked_example(client, auth_headers)
    await _patch(client, auth_headers, accounts["savings"]["id"], include_in_available=False)
    params = [("account_ids", accounts["checking"]["id"]), ("account_ids", accounts["savings"]["id"])]
    body = (await client.get(URL, headers=auth_headers, params=params)).json()
    assert body["total_primary"] == 500.0
    assert [a["name"] for a in body["excluded_accounts"]] == ["Reserve"]
    # A collection holding only the card: nothing available.
    body = (await client.get(URL, headers=auth_headers, params={"account_ids": accounts["card"]["id"]})).json()
    assert body["total_primary"] == 0.0


@pytest.mark.asyncio
async def test_switching_off_moves_no_total_report_or_history(
    client: AsyncClient, auth_headers, session: AsyncSession
):
    accounts = await _worked_example(client, auth_headers)
    rows_before = await session.scalar(select(func.count()).select_from(Transaction))
    types_before = (await session.execute(select(Account.id, Account.type).order_by(Account.id))).all()
    savings_id = accounts["savings"]["id"]
    reads = [
        "/api/dashboard/summary",
        "/api/reports/net-worth",
        "/api/reports/cash-flow",
        "/api/reports/income-expenses",
        f"/api/accounts/{savings_id}/summary",
        f"/api/accounts/{savings_id}/balance-history",
        f"/api/accounts/{savings_id}/running-balance",
        "/api/transactions",
    ]
    before = {path: (await client.get(path, headers=auth_headers)).json() for path in reads}
    summary = before["/api/dashboard/summary"]
    assert summary["total_balance_primary"] == 1300.0
    assert summary["cash_balance_primary"] == 1600.0

    await _patch(client, auth_headers, savings_id, include_in_available=False)

    for path in reads:
        assert (await client.get(path, headers=auth_headers)).json() == before[path], path
    # No transaction was written and no type changed to represent the setting.
    assert await session.scalar(select(func.count()).select_from(Transaction)) == rows_before == 4
    assert (await session.execute(select(Account.id, Account.type).order_by(Account.id))).all() == types_before


@pytest.mark.asyncio
async def test_invalid_writes_are_refused(client: AsyncClient, auth_headers):
    accounts = await _worked_example(client, auth_headers)
    resp = await _patch(client, auth_headers, accounts["checking"]["id"], include_in_available=None)
    assert resp.status_code == 400
    resp = await _patch(client, auth_headers, accounts["card"]["id"], include_in_available=False)
    assert resp.status_code == 400
    assert "checking, savings and wallet" in resp.json()["detail"]
    resp = await _patch(client, auth_headers, accounts["checking"]["id"], include_in_available="nope")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_type_change_and_setting_travel_together(client: AsyncClient, auth_headers):
    card = await _create(client, auth_headers, "Spare card", "credit_card", "0")
    resp = await _patch(client, auth_headers, card["id"], type="wallet", include_in_available=False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "wallet"
    assert resp.json()["include_in_available"] is False


@pytest.mark.asyncio
async def test_off_account_retyped_as_card_is_simply_not_liquid(client: AsyncClient, auth_headers):
    savings = await _create(client, auth_headers, "Reserve", "savings", "0")
    await _patch(client, auth_headers, savings["id"], include_in_available=False)
    resp = await _patch(client, auth_headers, savings["id"], type="credit_card")
    assert resp.status_code == 200, resp.text
    body = (await client.get(URL, headers=auth_headers)).json()
    assert body["accounts"] == [] and body["excluded_accounts"] == []
    # Back to savings, the stored choice is still there.
    resp = await _patch(client, auth_headers, savings["id"], type="savings")
    assert resp.json()["include_in_available"] is False


@pytest.mark.asyncio
async def test_closed_account_never_counts_and_keeps_its_setting(client: AsyncClient, auth_headers):
    accounts = await _worked_example(client, auth_headers)
    savings_id = accounts["savings"]["id"]
    await _patch(client, auth_headers, savings_id, include_in_available=False)
    assert (await client.post(f"/api/accounts/{savings_id}/close", headers=auth_headers)).status_code == 200
    body = (await client.get(URL, headers=auth_headers)).json()
    assert body["total_primary"] == 600.0
    assert body["excluded_accounts"] == []
    reopened = await client.post(f"/api/accounts/{savings_id}/reopen", headers=auth_headers)
    assert reopened.json()["include_in_available"] is False


@pytest.mark.asyncio
async def test_connected_account_setting_is_writable(client: AsyncClient, auth_headers, test_account):
    resp = await _patch(client, auth_headers, str(test_account.id), include_in_available=False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["include_in_available"] is False
    # The provider still owns the rest.
    resp = await _patch(client, auth_headers, str(test_account.id), name="Renamed")
    assert resp.status_code == 400
    body = (await client.get(URL, headers=auth_headers)).json()
    assert [a["id"] for a in body["excluded_accounts"]] == [str(test_account.id)]


@pytest.mark.asyncio
async def test_viewer_reads_but_cannot_write(client: AsyncClient, auth_headers, viewer_auth_headers):
    accounts = await _worked_example(client, auth_headers)
    assert (await client.get(URL, headers=viewer_auth_headers)).status_code == 200
    resp = await _patch(client, viewer_auth_headers, accounts["savings"]["id"], include_in_available=False)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_access_token_scopes(client: AsyncClient, auth_headers):
    accounts = await _worked_example(client, auth_headers)

    async def token(scopes: list[str]) -> dict:
        resp = await client.post("/api/access-tokens", headers=auth_headers, json={"name": "script", "scopes": scopes})
        assert resp.status_code == 201, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}

    reader = await token(["accounts:read"])
    assert (await client.get(URL, headers=reader)).status_code == 200
    resp = await _patch(client, reader, accounts["savings"]["id"], include_in_available=False)
    assert resp.status_code == 403
    writer = await token(["accounts:write"])
    resp = await _patch(client, writer, accounts["savings"]["id"], include_in_available=False)
    assert resp.status_code == 200
    other = await token(["transactions:read"])
    assert (await client.get(URL, headers=other)).status_code == 403


@pytest.mark.asyncio
async def test_workspace_isolation(client: AsyncClient, auth_headers, session: AsyncSession):
    await _create(client, auth_headers, "Current account", "checking", "500.00")
    stranger = User(
        id=uuid.uuid4(),
        email="stranger@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(stranger)
    await session.flush()
    foreign_ws = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=stranger.id,
        default_currency="BRL", locale="pt-BR",
    )
    session.add(foreign_ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=foreign_ws.id, user_id=stranger.id, role="owner"))
    foreign = Account(
        id=uuid.uuid4(), user_id=stranger.id, workspace_id=foreign_ws.id, name="Foreign",
        type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(foreign)
    await session.commit()

    resp = await _patch(client, auth_headers, str(foreign.id), include_in_available=False)
    assert resp.status_code == 404
    body = (await client.get(URL, headers=auth_headers, params={"account_ids": str(foreign.id)})).json()
    assert body["total_primary"] == 0.0 and body["accounts"] == []
    await session.refresh(foreign)
    assert foreign.include_in_available is True


@pytest.mark.asyncio
async def test_route_is_not_taken_by_the_account_id_route(client: AsyncClient, auth_headers):
    resp = await client.get(URL, headers=auth_headers)
    assert resp.status_code == 200, resp.text


async def _restore(client: AsyncClient, headers: dict, archive: bytes) -> uuid.UUID:
    resp = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", archive, "application/zip")},
        data={"content": "data", "mode": "new_workspace"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["workspace_id"])


@pytest.mark.asyncio
async def test_backup_round_trip_keeps_the_setting(client: AsyncClient, auth_headers, session: AsyncSession):
    savings = await _create(client, auth_headers, "Reserve", "savings", "1000.00")
    await _patch(client, auth_headers, savings["id"], include_in_available=False)
    archive = (await client.get("/api/export/backup?content=data", headers=auth_headers)).content
    restored_ws = await _restore(client, auth_headers, archive)
    restored = (await session.execute(select(Account).where(Account.workspace_id == restored_ws))).scalar_one()
    assert restored.include_in_available is False


@pytest.mark.asyncio
async def test_backup_without_the_column_restores_as_included(client: AsyncClient, auth_headers, session: AsyncSession):
    savings = await _create(client, auth_headers, "Reserve", "savings", "1000.00")
    await _patch(client, auth_headers, savings["id"], include_in_available=False)
    archive = (await client.get("/api/export/backup?content=data", headers=auth_headers)).content

    # Rewrite the archive the way an upstream (pre-f001) export looks.
    src = zipfile.ZipFile(io.BytesIO(archive))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith("accounts.json"):
                rows = json.loads(data)
                for row in rows:
                    row.pop("include_in_available", None)
                data = json.dumps(rows).encode()
            dst.writestr(info, data)
    restored_ws = await _restore(client, auth_headers, out.getvalue())
    restored = (await session.execute(select(Account).where(Account.workspace_id == restored_ws))).scalar_one()
    assert restored.include_in_available is True


@pytest.mark.asyncio
async def test_currencies_match_the_dashboard_rule_and_mcp(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user
):
    import mcp_server.tools  # noqa: F401
    from mcp_server.auth import CallContext
    from mcp_server.registry import REGISTRY

    from app.core.app_clock import app_today
    from app.models.fx_rate import FxRate

    session.add(FxRate(base_currency="USD", quote_currency="BRL", date=app_today(), rate=Decimal("5.0"), source="test"))
    await session.commit()
    await _create(client, auth_headers, "Current account", "checking", "500.00")
    for name, type_, balance, currency in (
        ("Dollar account", "checking", "33.33", "USD"),
        ("Euro savings", "savings", "100.00", "EUR"),  # no rate: summed at its native amount
        ("Dollar reserve", "savings", "1000.00", "USD"),
    ):
        resp = await client.post(
            "/api/accounts", headers=auth_headers,
            json={"name": name, "type": type_, "balance": balance, "currency": currency},
        )
        assert resp.status_code == 201, resp.text
        if name == "Dollar reserve":
            await _patch(client, auth_headers, resp.json()["id"], include_in_available=False)

    body = (await client.get(URL, headers=auth_headers)).json()
    # 500.00 + 33.33 x 5 = 166.65 + 100.00 unconverted EUR.
    assert body["total_primary"] == 766.65
    assert body["by_currency"] == {"BRL": 500.0, "USD": 33.33, "EUR": 100.0}
    assert body["fx_fallback"] is True
    assert body["unconverted_currencies"] == ["EUR"]

    # The dashboard headline rule over the same account list.
    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    dashboard = sum(
        Decimal(str(a["balance_primary"] if a["balance_primary"] is not None else a["current_balance"]))
        for a in listed
        if a["type"] in {"checking", "savings", "wallet"} and a["include_in_available"]
    )
    assert dashboard == Decimal("766.65")

    ctx = CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())
    mcp = await REGISTRY["get_available_balance"].handler(session=session, ctx=ctx)
    assert mcp["available_balance"] == body["total_primary"]
    assert mcp["by_currency"] == body["by_currency"]
    assert mcp["unconverted_currencies"] == ["EUR"]
