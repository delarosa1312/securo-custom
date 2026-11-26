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

    # The checking row counts tomorrow; the card row is debt already owed, like a posted one of that day.
    assert _ending(forecast, date(2026, 11, 20)) == Decimal("1500.00")
    assert _ending(forecast, date(2026, 11, 21)) == Decimal("1450.00")
    assert _ending(forecast, date(2026, 12, 31)) == Decimal("1450.00")


async def _card(b):
    return await data.account(
        b.session, b.user, b.ws, "Card", acct_type="credit_card", statement_close_day=1, payment_due_day=10,
    )


@pytest.mark.asyncio
async def test_a_card_purchase_of_today_or_earlier_counts_the_same_pending_or_posted(
    session, test_user, test_workspace, today,
):
    b = await pdata.Base(session, test_user, test_workspace).build()
    row = await b.txn(await _card(b), "30", date(2026, 11, 19), status="pending")
    pending = _ending(await _forecast(session, test_workspace), date(2026, 11, 30))
    row.status = "posted"
    await session.commit()
    posted = _ending(await _forecast(session, test_workspace), date(2026, 11, 30))
    assert pending == posted == Decimal("1500.00")


@pytest.mark.asyncio
async def test_card_spending_counts_but_card_credits_do_not(session, test_user, test_workspace, today):
    b = await pdata.Base(session, test_user, test_workspace).build()
    card = await _card(b)
    await b.txn(card, "200", date(2026, 11, 10))
    await b.txn(card, "200", date(2026, 11, 25), "credit", status="pending")  # refund of the 11-10 purchase
    await b.txn(card, "45", date(2026, 11, 27), status="pending")  # spending after today
    # Paying the bill moves no cash the forecast has not counted: card spending counts when it happens.
    await b.transfer(b.checking, card, "800", date(2026, 12, 10), status="pending")
    forecast = await _forecast(session, test_workspace)

    assert _ending(forecast, date(2026, 11, 30)) == Decimal("1455.00")
    assert _ending(forecast, date(2026, 12, 31)) == Decimal("1455.00")


@pytest.mark.asyncio
async def test_detected_patterns_come_only_from_covered_accounts(session, test_user, test_workspace, today):
    b = await pdata.Base(session, test_user, test_workspace).build()
    broker = await data.account(session, test_user, test_workspace, "Broker", acct_type="investment")
    for month in (7, 8, 9, 10, 11):
        await _named(b, broker, "40", date(2026, month, 10), "Dividend Synthetic", txn_type="credit")
    forecast = await _forecast(session, test_workspace)

    assert _ending(forecast, date(2026, 12, 31)) == forecast.summary.starting_liquid_balance
