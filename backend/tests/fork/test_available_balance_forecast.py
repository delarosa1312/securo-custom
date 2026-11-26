"""F-001 in the predictive forecast: the starting balance and runway follow the
setting; card debt is reported as before. Synthetic accounts only."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.cashflow_forecast_service import generate_cashflow_forecast


async def _account(session, user, ws, name, type_, opening, *, include=True):
    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, name=name, type=type_,
        balance=Decimal(opening), currency="BRL", include_in_available=include,
    )
    session.add(account)
    await session.flush()
    is_credit = type_ not in {"credit_card", "loan"}
    opening_date = app_today() - timedelta(days=30)
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=account.id,
            description="Saldo inicial", amount=Decimal(opening), amount_primary=Decimal(opening),
            currency="BRL", date=opening_date, effective_date=opening_date,
            type="credit" if is_credit else "debit", source="opening_balance", status="posted",
        )
    )
    return account


async def _forecast(session, ws, **kwargs):
    return await generate_cashflow_forecast(
        session, ws.id, horizon_days=90, include_discretionary_burn=False,
        reference_date=app_today(), **kwargs,
    )


@pytest.mark.asyncio
async def test_starting_balance_leaves_the_reserve_out(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Current account", "checking", "500.00")
    await _account(session, test_user, test_workspace, "Cash", "wallet", "100.00")
    reserve = await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00", include=False)
    await _account(session, test_user, test_workspace, "Card", "credit_card", "300.00")
    await session.commit()

    summary = (await _forecast(session, test_workspace)).summary
    assert summary.starting_liquid_balance == Decimal("600.00")
    assert summary.credit_card_balance == Decimal("-300.00")

    reserve.include_in_available = True
    await session.commit()
    summary = (await _forecast(session, test_workspace)).summary
    assert summary.starting_liquid_balance == Decimal("1600.00")
    assert summary.credit_card_balance == Decimal("-300.00")


@pytest.mark.asyncio
async def test_runway_counts_down_from_available_money(session, test_user, test_workspace):
    checking = await _account(session, test_user, test_workspace, "Current account", "checking", "100.00")
    reserve = await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00", include=False)
    first = app_today() + timedelta(days=10)
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=checking.id, description="Rent", amount=Decimal("500.00"), currency="BRL",
            type="debit", frequency="monthly", start_date=first, next_occurrence=first, is_active=True,
        )
    )
    await session.commit()

    off = (await _forecast(session, test_workspace)).summary
    assert off.has_shortfall_risk is True
    assert off.runway_days == 10
    assert off.first_shortfall_date == first

    reserve.include_in_available = True
    await session.commit()
    on = (await _forecast(session, test_workspace)).summary
    # 1,100 covers two rents (days 10 and about 40) and runs short on the third.
    assert on.runway_days is not None and on.runway_days > 60


@pytest.mark.asyncio
async def test_card_with_a_stored_off_value_still_reports_its_debt(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Current account", "checking", "500.00")
    await _account(session, test_user, test_workspace, "Card", "credit_card", "300.00", include=False)
    await session.commit()
    summary = (await _forecast(session, test_workspace)).summary
    assert summary.starting_liquid_balance == Decimal("500.00")
    assert summary.credit_card_balance == Decimal("-300.00")


def _posted(user, ws, account, description, amount, type_, tx_date):
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=account.id,
        description=description, payee=description, amount=Decimal(amount), amount_primary=Decimal(amount),
        currency="BRL", date=tx_date, effective_date=tx_date, type=type_, source="manual", status="posted",
    )


def _events(response) -> list[str]:
    return [event for point in response.daily_trajectory for event in point.events]


# Decision Q6 (Matheus, option B): everything about a switched-off account leaves the forecast.

@pytest.mark.asyncio
async def test_scheduled_flows_of_an_off_account_stay_out(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Current account", "checking", "500.00")
    await _account(session, test_user, test_workspace, "Cash", "wallet", "100.00")
    reserve = await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00", include=False)
    day5 = app_today() + timedelta(days=5)
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=reserve.id, description="Reserve interest", amount=Decimal("10.00"), currency="BRL",
            type="credit", frequency="monthly", start_date=day5, next_occurrence=day5, is_active=True,
        )
    )
    await session.commit()

    off = await _forecast(session, test_workspace)
    assert off.daily_trajectory[5].ending_balance == Decimal("600.00")
    assert not any("Reserve interest" in e for e in _events(off))

    reserve.include_in_available = True
    await session.commit()
    on = await _forecast(session, test_workspace)
    assert on.daily_trajectory[5].ending_balance == Decimal("1610.00")


@pytest.mark.asyncio
async def test_transfer_into_an_off_account_still_leaves_available_money(session, test_user, test_workspace):
    checking = await _account(session, test_user, test_workspace, "Current account", "checking", "500.00")
    await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00", include=False)
    day3 = app_today() + timedelta(days=3)
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=checking.id, description="Move to reserve", amount=Decimal("200.00"), currency="BRL",
            type="debit", frequency="monthly", start_date=day3, next_occurrence=day3, is_active=True,
        )
    )
    await session.commit()
    forecast = await _forecast(session, test_workspace)
    assert forecast.daily_trajectory[3].ending_balance == Decimal("300.00")


@pytest.mark.asyncio
async def test_spending_history_of_an_off_account_stays_out_of_the_burn(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Current account", "checking", "5000.00")
    reserve = await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00", include=False)
    session.add(_posted(test_user, test_workspace, reserve, "Home repair", "900.00", "debit", app_today() - timedelta(days=10)))
    await session.commit()

    off = await generate_cashflow_forecast(session, test_workspace.id, horizon_days=30, reference_date=app_today())
    assert off.summary.total_projected_discretionary == Decimal("0.00")

    reserve.include_in_available = True
    await session.commit()
    on = await generate_cashflow_forecast(session, test_workspace.id, horizon_days=30, reference_date=app_today())
    # 900 over the 90-day window = 10.00 a day.
    assert on.daily_trajectory[1].discretionary_burn == Decimal("10.00")


@pytest.mark.asyncio
async def test_detected_pattern_of_an_off_account_stays_out(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Current account", "checking", "500.00")
    reserve = await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00")
    for weeks_back in (6, 4, 2):
        paid = app_today() - timedelta(weeks=weeks_back)
        session.add(_posted(test_user, test_workspace, reserve, "Reserve Yield Co", "25.00", "credit", paid))
    await session.commit()

    on = await _forecast(session, test_workspace)
    assert any("Reserve Yield Co" in e for e in _events(on))

    reserve.include_in_available = False
    await session.commit()
    off = await _forecast(session, test_workspace)
    assert not any("Reserve Yield Co" in e for e in _events(off))
    assert off.summary.total_projected_inflow == Decimal("0.00")


@pytest.mark.asyncio
async def test_pattern_split_over_two_accounts_keeps_only_the_included_share(session, test_user, test_workspace):
    # One payee paying into both accounts on the same day must not hand the
    # whole pattern to whichever row sorts last.
    checking = await _account(session, test_user, test_workspace, "Current account", "checking", "500.00")
    reserve = await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00", include=False)
    for weeks_back in (6, 4, 2):
        paid = app_today() - timedelta(weeks=weeks_back)
        session.add(_posted(test_user, test_workspace, checking, "Acme Payroll", "4000.00", "credit", paid))
        session.add(_posted(test_user, test_workspace, reserve, "Acme Payroll", "1000.00", "credit", paid))
    await session.commit()

    forecast = await _forecast(session, test_workspace)
    payroll = [e for e in _events(forecast) if "Acme Payroll" in e]
    assert payroll and all(e.startswith("+4000.00 ") for e in payroll)
