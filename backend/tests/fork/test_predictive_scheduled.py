"""F-006 (#8): the predictive forecast's scheduled layer. Synthetic data only; today is 2026-11-20."""
from datetime import date
from decimal import Decimal

import pytest

from app.services.cashflow_forecast_service import generate_cashflow_forecast
from tests.fork import _budget_data as data
from tests.fork import _projection_data as pdata


async def _forecast(session, ws, days=45):
    return await generate_cashflow_forecast(session, ws.id, horizon_days=days, include_discretionary_burn=False)


def _ending(forecast, on: date) -> Decimal:
    return next(p.ending_balance for p in forecast.daily_trajectory if p.date == on)


@pytest.fixture
def today(monkeypatch):
    return data.pin_today(monkeypatch)


async def _named(b, acct, amount, when, name, **extra):
    row = await b.txn(acct, amount, when, **extra)
    row.description = row.payee = name
    await b.session.commit()
    return row


@pytest.mark.asyncio
async def test_detected_pattern_yields_to_a_pending_row_of_the_same_bill(session, test_user, test_workspace, today):
    b = await pdata.Base(session, test_user, test_workspace).build()
    for month in (8, 9, 10, 11):
        await _named(b, b.checking, "50", date(2026, month, 10), "Gym Synthetic")
    await _named(b, b.checking, "50", date(2026, 12, 10), "Gym Synthetic", status="pending")
    forecast = await _forecast(session, test_workspace)

    # Start 1,300 (1,500 less four posted gym payments); December's gym once, not twice.
    assert _ending(forecast, date(2026, 12, 31)) == Decimal("1250.00")
    assert sum(len([e for e in p.events if "Gym" in e]) for p in forecast.daily_trajectory) == 1


@pytest.mark.asyncio
async def test_switched_off_account_rows_stay_out_but_a_transfer_into_it_leaves(session, test_user, test_workspace,
                                                                               today):
    b = await pdata.Base(session, test_user, test_workspace).build()
    b.savings.include_in_available = False
    await session.commit()
    await b.txn(b.savings, "70", date(2026, 11, 26), status="pending")
    await b.transfer(b.checking, b.savings, "200", date(2026, 12, 3), status="pending")
    forecast = await _forecast(session, test_workspace)

    assert forecast.summary.starting_liquid_balance == Decimal("1000.00")
    assert _ending(forecast, date(2026, 12, 31)) == Decimal("800.00")


@pytest.mark.asyncio
async def test_overdue_rows_count_tomorrow_and_only_on_covered_accounts(session, test_user, test_workspace, today):
    b = await pdata.Base(session, test_user, test_workspace).build()
    card = await data.account(session, test_user, test_workspace, "Card", acct_type="credit_card")
    broker = await data.account(session, test_user, test_workspace, "Broker", acct_type="investment")
    await b.txn(b.checking, "50", date(2026, 11, 10), status="pending")
    await b.txn(card, "30", date(2026, 11, 19), status="pending")
    await b.txn(broker, "500", date(2026, 11, 25), "credit", status="pending")
    forecast = await _forecast(session, test_workspace)

    assert _ending(forecast, date(2026, 11, 20)) == Decimal("1500.00")
    assert _ending(forecast, date(2026, 11, 21)) == Decimal("1420.00")
    assert _ending(forecast, date(2026, 12, 31)) == Decimal("1420.00")
