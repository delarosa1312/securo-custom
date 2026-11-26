"""Budget-aware Cash Flow mode over REST and MCP, with access checks (F-002). Synthetic data only."""
from datetime import date

import pytest

from app.services import access_token_service
from mcp_server.auth import CallContext
from mcp_server.tools.reports import get_cash_flow as mcp_get_cash_flow

from tests.fork import _budget_data as data

URL = "/api/reports/cash-flow"


@pytest.fixture
def today(monkeypatch):
    return data.pin_today(monkeypatch)


async def _seed(session, user, ws):
    acct = await data.account(session, user, ws)
    restaurants = await data.category(session, user, ws, "Restaurants")
    await data.txn(session, user, ws, acct, "5100", date(2026, 10, 1), "credit", source="opening_balance")
    await data.txn(session, user, ws, acct, "100", date(2026, 11, 10), cat=restaurants)
    await data.txn(session, user, ws, acct, "40", date(2026, 11, 25), cat=restaurants, status="pending")
    await data.budget(session, user, ws, restaurants, 250)
    return acct


async def _token(session, user, ws, scopes):
    _, raw = await access_token_service.create_token(
        session, workspace_id=ws.id, user_id=user.id, can_write=False,
        name="synthetic", scopes=scopes, expires_in_days=1,
    )
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_rest_off_is_byte_identical(client, auth_headers, session, test_user, test_workspace, today):
    await _seed(session, test_user, test_workspace)
    plain = await client.get(URL, params={"months": 2}, headers=auth_headers)
    off = await client.get(URL, params={"months": 2, "budget_forecast": "false"}, headers=auth_headers)

    assert plain.status_code == 200 and off.status_code == 200
    assert plain.content == off.content
    assert b"budget" not in plain.content


@pytest.mark.asyncio
async def test_rest_on_returns_the_forecast_and_explanation(client, auth_headers, session, test_user, test_workspace, today):
    await _seed(session, test_user, test_workspace)
    response = await client.get(URL, params={"months": 1, "budget_forecast": "true"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["budget_forecast"]["placement"] == "subtracted"
    assert body["budget_forecast"]["rule"] == "even_daily"
    nov = body["budget_forecast"]["months"][0]
    assert nov["month"] == "2026-11" and nov["days"] == 10
    restaurants = nov["categories"][0]
    # Exact decimals travel as strings.
    assert (restaurants["budget"], restaurants["posted"], restaurants["scheduled"], restaurants["remaining"]) == (
        "250.00", "100.00", "40.00", "110.00"
    )
    point = next(p for p in body["trend"] if p["date"] == "2026-11-21")
    assert point["breakdowns"]["budget_outflow"] == 11.0
    keys = [b["key"] for b in body["summary"]["breakdowns"]]
    assert keys == ["startingBalance", "projectedIncome", "projectedExpenses", "budgetExpenses", "endingBalance"]


@pytest.mark.asyncio
async def test_rest_filtered_view_is_unallocated(client, auth_headers, session, test_user, test_workspace, today):
    acct = await _seed(session, test_user, test_workspace)
    response = await client.get(
        URL, params={"months": 1, "budget_forecast": "true", "account_ids": str(acct.id)}, headers=auth_headers
    )
    body = response.json()
    assert body["budget_forecast"]["placement"] == "unallocated"
    assert "unallocatedBudget" in [b["key"] for b in body["summary"]["breakdowns"]]


@pytest.mark.asyncio
async def test_baseline_and_budgets_cannot_be_combined(client, auth_headers, session, test_user, test_workspace, today):
    await _seed(session, test_user, test_workspace)
    response = await client.get(
        URL, params={"budget_forecast": "true", "baseline": "true"}, headers=auth_headers
    )
    assert response.status_code == 422
    assert "baseline" in response.json()["detail"]
    alone = await client.get(URL, params={"baseline": "true"}, headers=auth_headers)
    assert alone.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("months, status", [(3, 200), (6, 200), (12, 200), (24, 200), (25, 422), (0, 422)])
async def test_horizons(client, auth_headers, session, test_user, test_workspace, today, months, status):
    await _seed(session, test_user, test_workspace)
    response = await client.get(
        URL, params={"months": months, "interval": "monthly", "budget_forecast": "true"}, headers=auth_headers
    )
    assert response.status_code == status
    if status == 200:
        assert len(response.json()["budget_forecast"]["months"]) == months + 1


@pytest.mark.asyncio
async def test_viewer_can_read_it(client, viewer_auth_headers, session, test_user, test_workspace, today):
    await _seed(session, test_user, test_workspace)
    headers = {**viewer_auth_headers, "X-Workspace-Id": str(test_workspace.id)}
    response = await client.get(URL, params={"months": 1, "budget_forecast": "true"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["budget_forecast"]["total"] == "360.00"


@pytest.mark.asyncio
async def test_access_token_needs_budget_scope(client, session, test_user, test_workspace, today):
    await _seed(session, test_user, test_workspace)
    reports_only = await _token(session, test_user, test_workspace, ["reports:read"])
    both = await _token(session, test_user, test_workspace, ["reports:read", "budgets:read"])

    assert (await client.get(URL, params={"months": 1}, headers=reports_only)).status_code == 200
    refused = await client.get(URL, params={"months": 1, "budget_forecast": "true"}, headers=reports_only)
    assert refused.status_code == 403
    allowed = await client.get(URL, params={"months": 1, "budget_forecast": "true"}, headers=both)
    assert allowed.status_code == 200
    assert allowed.json()["budget_forecast"]["total"] == "360.00"


@pytest.mark.asyncio
async def test_mcp_matches_rest(client, auth_headers, session, test_user, test_workspace, today):
    await _seed(session, test_user, test_workspace)
    ctx = CallContext(user_id=test_user.id, workspace_id=test_workspace.id)
    for params in ({"months": 3, "interval": "weekly"}, {"months": 1, "interval": "daily"}):
        rest = await client.get(URL, params={**params, "budget_forecast": "true"}, headers=auth_headers)
        mcp = await mcp_get_cash_flow(session=session, ctx=ctx, budget_forecast=True, **params)
        assert mcp == rest.json()
        rest_off = await client.get(URL, params=params, headers=auth_headers)
        mcp_off = await mcp_get_cash_flow(session=session, ctx=ctx, **params)
        assert mcp_off == rest_off.json()
        assert "budget_forecast" not in mcp_off


@pytest.mark.asyncio
@pytest.mark.parametrize("months", ["13", "24", "0", "-1", "abc", "12.5"])
async def test_months_refusals_without_the_mode_match_upstream(client, auth_headers, months):
    """The route accepts 24 months only with the mode; without it, upstream's exact 422 body."""
    from fastapi import FastAPI, Query
    from httpx import ASGITransport, AsyncClient

    upstream = FastAPI()

    @upstream.get(URL)
    async def upstream_route(months: int = Query(6, ge=1, le=12)):
        return {}

    async with AsyncClient(transport=ASGITransport(app=upstream), base_url="http://test") as reference:
        expected = await reference.get(URL, params={"months": months})
    actual = await client.get(URL, params={"months": months}, headers=auth_headers)
    assert expected.status_code == 422
    assert actual.status_code == 422
    assert actual.content == expected.content
