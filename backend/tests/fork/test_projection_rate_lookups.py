"""F-006 (#8): the chart of a far month looks a rate up once per currency, not once per occurrence."""
from datetime import date

import pytest

from app.services import dashboard_service, fx_rate_service
from tests.fork import _budget_data as data
from tests.fork import _projection_data as pdata


@pytest.mark.asyncio
async def test_far_month_chart_does_not_grow_rate_lookups(session, test_user, test_workspace, monkeypatch):
    data.pin_today(monkeypatch)
    b = await pdata.Base(session, test_user, test_workspace).build()
    usd = await data.account(session, test_user, test_workspace, "USD", currency="USD")
    await data.usd_rate(session, "BRL", "5", when=date(2026, 9, 1))
    for day in (22, 23, 24):
        rule = await b.rule(usd, "10", date(2026, 11, day))
        rule.frequency = "weekly"
    await session.commit()
    calls = {"n": 0}
    original = fx_rate_service.get_rate

    async def counting(*args, **kwargs):
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(fx_rate_service, "get_rate", counting)
    counts = {}
    for month in (date(2026, 12, 1), date(2027, 11, 1)):
        calls["n"] = 0
        await dashboard_service.get_balance_history(session, test_workspace.id, test_user.id, month=month)
        counts[month] = calls["n"]
    # About 50 weekly occurrences sit between today and November 2027; the in-month ones are the same few.
    assert counts[date(2027, 11, 1)] <= counts[date(2026, 12, 1)] + 2, counts
