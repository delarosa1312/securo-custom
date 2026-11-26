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
