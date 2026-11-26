"""F-004: a transfer schedule in every projection view. Synthetic data.

Checking 1,000 and Savings 1,000 (BRL); "Savings plan" moves 200 from Checking to Savings on the
10th of next month. Consolidated: no income, no expense, total unchanged. Per account: Checking
-200, Savings +200.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.services import (
    budget_service,
    dashboard_service,
    recurring_match_service,
    report_service,
    transaction_calendar_service,
)
from app.services.cashflow_forecast_service import generate_cashflow_forecast
from tests.fork._transfer_data import account, next_month_day, transfer_schedule


async def _setup(session, user, ws, **kwargs):
    checking = await account(session, user, ws, "Checking")
    savings = await account(session, user, ws, "Savings", type_="savings", **kwargs)
    rec = await transfer_schedule(session, user, ws, checking, savings)
    await session.commit()
    return checking, savings, rec


def _brl(balance: dict) -> float:
    return round(balance.get("BRL", 0.0), 2)


@pytest.mark.asyncio
async def test_dashboard_moves_both_accounts_and_no_pnl(session, test_user, test_workspace):
    checking, savings, _ = await _setup(session, test_user, test_workspace)
    month = next_month_day(1)

    whole = await dashboard_service.get_summary(session, test_workspace.id, test_user.id, month=month)
    assert whole.projected_income == whole.monthly_income
    assert whole.projected_expenses == whole.monthly_expenses == 0
    assert _brl(whole.projected_balance) == _brl(whole.total_balance) == 2000.0

    only_checking = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=month, account_ids=[checking.id]
    )
    assert _brl(only_checking.projected_balance) == 800.0
    assert only_checking.projected_expenses == 0
    only_savings = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=month, account_ids=[savings.id]
    )
    assert _brl(only_savings.projected_balance) == 1200.0
    assert only_savings.projected_income == 0


@pytest.mark.asyncio
async def test_upcoming_lists_one_leg_per_account(session, test_user, test_workspace):
    checking, savings, rec = await _setup(session, test_user, test_workspace)
    month = next_month_day(1)
    rows = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, month
    )
    assert sorted((r.account_id, r.type, r.amount, r.category_treat_as_transfer) for r in rows) == sorted([
        (str(checking.id), "debit", 200.0, True),
        (str(savings.id), "credit", 200.0, True),
    ])
    assert {r.recurring_id for r in rows} == {str(rec.id)}
    one = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, account_id=savings.id,
        from_date=month, to_date=month + timedelta(days=27),
    )
    assert [(r.account_id, r.type) for r in one] == [(str(savings.id), "credit")]


@pytest.mark.asyncio
async def test_generated_pair_suppresses_both_projected_legs(session, test_user, test_workspace):
    checking, savings, rec = await _setup(session, test_user, test_workspace)
    day = next_month_day(10)
    pair = uuid.uuid4()
    for acc, tx_type in ((checking, "debit"), (savings, "credit")):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=acc.id,
            description="Savings plan", amount=Decimal("200"), currency="BRL", date=day,
            effective_date=day, type=tx_type, source="recurring", status="posted",
            transfer_pair_id=pair, recurring_transaction_id=rec.id,
        ))
    await session.commit()
    rows = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, next_month_day(1)
    )
    assert rows == []
    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=next_month_day(1), account_ids=[checking.id]
    )
    assert _brl(summary.projected_balance) == 800.0  # the posted future row, not twice


@pytest.mark.asyncio
async def test_calendar_shows_both_legs_as_transfers(session, test_user, test_workspace):
    checking, savings, _ = await _setup(session, test_user, test_workspace)
    month = next_month_day(1)
    calendar = await transaction_calendar_service.get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=month
    )
    day = next(d for d in calendar.days if d.date == next_month_day(10))
    projected = [item for item in day.items if item.kind == "projected"]
    assert sorted((str(i.account_id), i.type, i.is_transfer) for i in projected) == sorted([
        (str(checking.id), "debit", True), (str(savings.id), "credit", True),
    ])
    assert day.projected_income == day.projected_expense == 0
    last = [d for d in calendar.days if d.in_month][-1]
    assert round(last.ending_balance, 2) == 2000.0

    only_checking = await transaction_calendar_service.get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=month, account_ids=[checking.id]
    )
    last = [d for d in only_checking.days if d.in_month][-1]
    assert round(last.ending_balance, 2) == 800.0


@pytest.mark.asyncio
@pytest.mark.parametrize("savings_available, outflow", [(True, Decimal("0")), (False, Decimal("200"))])
async def test_predictive_counts_only_money_leaving_available(
    session, test_user, test_workspace, savings_available, outflow
):
    await _setup(session, test_user, test_workspace, include=savings_available)
    forecast = await generate_cashflow_forecast(
        session, test_workspace.id, horizon_days=(next_month_day(10) - app_today()).days + 5,
        include_discretionary_burn=False, reference_date=app_today(),
    )
    assert forecast.summary.total_projected_outflow == outflow
    assert forecast.summary.total_projected_inflow == 0


@pytest.mark.asyncio
async def test_predictive_counts_money_arriving_from_a_switched_off_account(
    session, test_user, test_workspace
):
    checking = await account(session, test_user, test_workspace, "Checking")
    reserve = await account(session, test_user, test_workspace, "Reserve", type_="savings", include=False)
    await transfer_schedule(session, test_user, test_workspace, reserve, checking)
    await session.commit()
    forecast = await generate_cashflow_forecast(
        session, test_workspace.id, horizon_days=(next_month_day(10) - app_today()).days + 5,
        include_discretionary_burn=False, reference_date=app_today(),
    )
    assert forecast.summary.total_projected_inflow == Decimal("200")
    assert forecast.summary.total_projected_outflow == 0


@pytest.mark.asyncio
async def test_closed_destination_projects_nothing(session, test_user, test_workspace):
    checking, savings, _ = await _setup(session, test_user, test_workspace)
    savings.is_closed = True
    await session.commit()
    rows = await dashboard_service.get_projected_transactions(
        session, test_workspace.id, test_user.id, next_month_day(1)
    )
    assert rows == []
    summary = await dashboard_service.get_summary(
        session, test_workspace.id, test_user.id, month=next_month_day(1), account_ids=[checking.id]
    )
    assert _brl(summary.projected_balance) == 1000.0


@pytest.mark.asyncio
async def test_cash_flow_report_is_as_without_the_schedule(session, test_user, test_workspace):
    """The Cash Flow report leaves transfers out, as it leaves out pending transfer pairs."""
    checking, _, rec = await _setup(session, test_user, test_workspace)

    async def reports():
        return [
            (
                await report_service.get_cash_flow_report(
                    session, test_workspace.id, test_user.id, months=3, interval="monthly",
                    currency="BRL", account_ids=ids,
                )
            ).model_dump()
            for ids in (None, [checking.id])
        ]

    with_schedule = await reports()
    rec.is_active = False
    await session.commit()
    assert with_schedule == await reports()


@pytest.mark.asyncio
async def test_spending_and_budgets_see_no_expense(session, test_user, test_workspace):
    await _setup(session, test_user, test_workspace)
    month = next_month_day(1)
    spending = await dashboard_service.get_spending_by_category(
        session, test_workspace.id, test_user.id, month
    )
    assert all(not getattr(item, "projected", 0) for item in spending)
    assert await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month
    ) == []


@pytest.mark.asyncio
async def test_bank_rows_never_merge_into_a_transfer(session, test_user, test_workspace):
    checking, savings, rec = await _setup(session, test_user, test_workspace)
    day = next_month_day(10)
    bill = await recurring_match_service.find_bill_for_incoming(
        session, test_user.id, checking.id, Decimal("200"), "BRL", "debit", day, "Savings plan",
    )
    assert bill is None
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=checking.id,
        description="Savings plan", amount=Decimal("200"), currency="BRL", date=day, effective_date=day,
        type="debit", source="recurring", status="pending", transfer_pair_id=uuid.uuid4(),
        recurring_transaction_id=rec.id,
    ))
    await session.commit()
    placeholder = await recurring_match_service.find_placeholder_for_incoming(
        session, checking.id, Decimal("200"), "BRL", "debit", day, "Savings plan",
    )
    assert placeholder is None
