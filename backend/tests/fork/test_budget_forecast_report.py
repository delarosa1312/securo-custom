"""Budget-aware Cash Flow mode through `get_cash_flow_report` (F-002). Synthetic data only.

Today is pinned to 2026-11-20 (30-day November, 10 days left from tomorrow).
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.fork.budget_forecast_schemas import CashFlowBudgetResponse
from app.models.budget import Budget
from app.models.category import Category
from app.schemas.report import ReportResponse
from app.services import admin_service, budget_service
from app.services.report_service import get_cash_flow_report

from tests.fork import _budget_data as data


@pytest.fixture
def today(monkeypatch):
    return data.pin_today(monkeypatch)


def _point(report, label):
    return next(p for p in report.trend if p.date == label)


def _breakdown(report, key):
    return next((b.value for b in report.summary.breakdowns if b.key == key), None)


def _category(report, month, name):
    m = next(m for m in report.budget_forecast.months if m.month == month)
    return next(c for c in m.categories if c.name == name)


async def _worked_example(session, user, ws):
    """Balance 5,000 today. Restaurants 250 (100 posted, 40 pending on the 25th),
    Groceries 600 (650 posted), Streaming 30 (recurring 30 on the 28th)."""
    acct = await data.account(session, user, ws)
    restaurants = await data.category(session, user, ws, "Restaurants")
    groceries = await data.category(session, user, ws, "Groceries")
    streaming = await data.category(session, user, ws, "Streaming")
    await data.txn(session, user, ws, acct, "5750", date(2026, 10, 1), "credit", source="opening_balance")
    await data.txn(session, user, ws, acct, "100", date(2026, 11, 10), cat=restaurants)
    await data.txn(session, user, ws, acct, "650", date(2026, 11, 5), cat=groceries)
    await data.txn(session, user, ws, acct, "40", date(2026, 11, 25), cat=restaurants, status="pending")
    await data.recurring(session, user, ws, acct, "30", date(2026, 11, 28), cat=streaming)
    for cat, amount in ((restaurants, 250), (groceries, 600), (streaming, 30)):
        await data.budget(session, user, ws, cat, amount)
    return acct, restaurants, groceries, streaming


async def _report(session, ws, user, **kwargs):
    kwargs.setdefault("months", 1)
    return await get_cash_flow_report(session, ws.id, user.id, **kwargs)


@pytest.mark.asyncio
async def test_worked_example(session, test_user, test_workspace, today):
    await _worked_example(session, test_user, test_workspace)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    assert isinstance(report, CashFlowBudgetResponse)
    assert _breakdown(report, "startingBalance") == 5000.0
    # 5,000 - 40 pending - 30 recurring - 110 remaining restaurant budget.
    assert _point(report, "2026-11-30").value == 4820.0
    assert _point(report, "2026-11-21").breakdowns["budget_outflow"] == 11.0
    assert _point(report, "2026-11-20").breakdowns["budget_outflow"] == 0.0
    # December: Restaurants 250 (8.07 x 14, 8.06 x 17) + Groceries 600 (19.36 x 15, 19.35 x 16).
    assert _point(report, "2026-12-01").breakdowns["budget_outflow"] == 27.43
    assert _point(report, "2026-12-31").breakdowns["budget_outflow"] == 27.41
    assert _breakdown(report, "projectedExpenses") == 100.0
    assert _breakdown(report, "budgetExpenses") == 960.0
    assert _breakdown(report, "endingBalance") == 3940.0
    assert report.summary.change_amount == -1060.0
    assert report.meta.lowest_balance == 3940.0

    nov = _category(report, "2026-11", "Restaurants")
    assert (nov.budget, nov.posted, nov.scheduled, nov.remaining, nov.forecast) == (
        Decimal("250.00"), Decimal("100.00"), Decimal("40.00"), Decimal("110.00"), Decimal("110.00")
    )
    over = _category(report, "2026-11", "Groceries")
    assert over.remaining == Decimal("0.00") and over.posted == Decimal("650.00")
    subscription = _category(report, "2026-11", "Streaming")
    assert subscription.scheduled == Decimal("30.00") and subscription.remaining == Decimal("0.00")

    month = report.budget_forecast.months[0]
    assert (month.spread_from, month.spread_to, month.days) == ("2026-11-21", "2026-11-30", 10)
    assert report.budget_forecast.placement == "subtracted"
    assert report.budget_forecast.total == Decimal("960.00")


@pytest.mark.asyncio
async def test_new_posted_spending_moves_from_remaining_to_balance(session, test_user, test_workspace, today):
    acct, restaurants, *_ = await _worked_example(session, test_user, test_workspace)
    before = await _report(session, test_workspace, test_user, budget_forecast=True)
    await data.txn(session, test_user, test_workspace, acct, "30", today, cat=restaurants)
    after = await _report(session, test_workspace, test_user, budget_forecast=True)

    assert _breakdown(after, "startingBalance") == _breakdown(before, "startingBalance") - 30
    assert _category(after, "2026-11", "Restaurants").remaining == Decimal("80.00")
    assert _point(after, "2026-11-21").breakdowns["budget_outflow"] == 8.0
    assert _point(after, "2026-11-30").value == _point(before, "2026-11-30").value == 4820.0
    assert _breakdown(after, "endingBalance") == _breakdown(before, "endingBalance")


@pytest.mark.asyncio
async def test_mode_off_is_the_upstream_report(session, test_user, test_workspace, today):
    await _worked_example(session, test_user, test_workspace)
    default = await _report(session, test_workspace, test_user, months=3)
    explicit_off = await _report(session, test_workspace, test_user, months=3, budget_forecast=False)

    assert type(default) is ReportResponse and type(explicit_off) is ReportResponse
    assert default.model_dump_json() == explicit_off.model_dump_json()
    assert "budget" not in default.model_dump_json()
    # With the mode off, budgets never move a number: same response without them.
    await session.execute(delete(Budget))
    await session.commit()
    without_budgets = await _report(session, test_workspace, test_user, months=3)
    assert without_budgets.model_dump_json() == default.model_dump_json()
    for cat_name, amount in (("Restaurants", 250), ("Groceries", 600), ("Streaming", 30)):
        cat = (await session.execute(select(Category).where(Category.name == cat_name))).scalar_one()
        await data.budget(session, test_user, test_workspace, cat, amount)
    on = await _report(session, test_workspace, test_user, months=3, budget_forecast=True)
    assert _breakdown(on, "startingBalance") == _breakdown(default, "startingBalance")
    assert _breakdown(on, "projectedExpenses") == _breakdown(default, "projectedExpenses")
    assert _breakdown(on, "projectedIncome") == _breakdown(default, "projectedIncome")
    for off_point, on_point in zip(default.trend, on.trend):
        assert on_point.breakdowns["inflow"] == off_point.breakdowns["inflow"]
        assert on_point.breakdowns["outflow"] == off_point.breakdowns["outflow"]
        assert on_point.value == pytest.approx(off_point.value - _cumulative_budget(on, on_point.date), abs=0.011)


def _cumulative_budget(report, label):
    total = 0.0
    for p in report.trend:
        total += p.breakdowns["budget_outflow"]
        if p.date == label:
            return total
    raise AssertionError(label)


@pytest.mark.asyncio
async def test_filtered_view_reports_unallocated_and_leaves_the_balance(session, test_user, test_workspace, today):
    acct, *_ = await _worked_example(session, test_user, test_workspace)
    off = await _report(session, test_workspace, test_user, account_ids=[acct.id])
    on = await _report(session, test_workspace, test_user, account_ids=[acct.id], budget_forecast=True)

    assert on.budget_forecast.placement == "unallocated"
    assert [p.value for p in on.trend] == [p.value for p in off.trend]
    assert _breakdown(on, "unallocatedBudget") == 960.0
    assert _breakdown(on, "budgetExpenses") is None
    assert _breakdown(on, "endingBalance") == _breakdown(off, "endingBalance")
    # Not in the points either: their flows net to the filtered balance's move.
    assert [p.breakdowns for p in on.trend] == [p.breakdowns for p in off.trend]
    # An empty filter (a collection with no bank account) behaves the same way.
    empty = await _report(session, test_workspace, test_user, account_ids=[], budget_forecast=True)
    assert empty.budget_forecast.placement == "unallocated"


@pytest.mark.asyncio
async def test_weekly_and_monthly_views_are_sums_of_the_days(session, test_user, test_workspace, today):
    await _worked_example(session, test_user, test_workspace)
    daily = await _report(session, test_workspace, test_user, budget_forecast=True)
    monthly = await _report(session, test_workspace, test_user, interval="monthly", budget_forecast=True)
    weekly = await _report(session, test_workspace, test_user, interval="weekly", budget_forecast=True)

    assert _point(monthly, "2026-11").breakdowns["budget_outflow"] == 110.0
    assert _point(monthly, "2026-12").breakdowns["budget_outflow"] == 850.0
    assert _point(monthly, "2026-12").value == _point(daily, "2026-12-31").value
    assert round(sum(p.breakdowns["budget_outflow"] for p in weekly.trend), 2) == 960.0
    # ISO week 48 of 2026 runs 23 to 29 November: 7 days at 11.00.
    assert _point(weekly, "2026-W48").breakdowns["budget_outflow"] == 77.0
    # Over six months the last partial week is an appended end point and still counts.
    long_weekly = await _report(session, test_workspace, test_user, months=6, interval="weekly", budget_forecast=True)
    long_total = sum((Decimal(str(p.breakdowns["budget_outflow"])) for p in long_weekly.trend), Decimal("0"))
    assert long_total == long_weekly.budget_forecast.total


@pytest.mark.asyncio
async def test_refunds_and_reimbursements(session, test_user, test_workspace, today):
    acct, restaurants, *_ = await _worked_example(session, test_user, test_workspace)
    reimbursements = await data.category(session, test_user, test_workspace, "Reimbursements")
    await data.txn(session, test_user, test_workspace, acct, "20", date(2026, 11, 12), "credit", cat=restaurants)
    await data.txn(session, test_user, test_workspace, acct, "50", date(2026, 11, 13), "credit", cat=reimbursements)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    nov = _category(report, "2026-11", "Restaurants")
    # Refund in the same category frees budget; a reimbursement elsewhere does not.
    assert nov.posted == Decimal("80.00") and nov.remaining == Decimal("130.00")


@pytest.mark.asyncio
async def test_category_lines_count_in_each_category(session, test_user, test_workspace, today):
    acct, restaurants, groceries, _ = await _worked_example(session, test_user, test_workspace)
    await data.split_txn(session, test_user, test_workspace, acct, date(2026, 11, 15), [
        (restaurants, "60"), (groceries, "40"),
    ])
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    assert _category(report, "2026-11", "Restaurants").posted == Decimal("160.00")
    assert _category(report, "2026-11", "Restaurants").remaining == Decimal("50.00")
    assert _category(report, "2026-11", "Groceries").posted == Decimal("690.00")


@pytest.mark.asyncio
async def test_category_groups_are_shown_not_budgeted(session, test_user, test_workspace, today):
    food = await data.group(session, test_user, test_workspace, "Food")
    await data.account(session, test_user, test_workspace)
    bakery = await data.category(session, test_user, test_workspace, "Bakery", group_id=food.id)
    await data.category(session, test_user, test_workspace, "Market", group_id=food.id)
    await data.budget(session, test_user, test_workspace, bakery, 31)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    dec = next(m for m in report.budget_forecast.months if m.month == "2026-12")
    assert [(c.name, c.group_name) for c in dec.categories] == [("Bakery", "Food")]
    assert dec.forecast == Decimal("31.00")


@pytest.mark.asyncio
async def test_budget_in_another_currency_converts_at_the_month_rate(session, test_user, test_workspace, today):
    brl = await data.account(session, test_user, test_workspace)
    usd = await data.account(session, test_user, test_workspace, "Travel card", currency="USD")
    travel = await data.category(session, test_user, test_workspace, "Travel")
    await data.usd_rate(session, "BRL", "5")
    await data.budget(session, test_user, test_workspace, travel, 100, currency="USD")
    await data.txn(session, test_user, test_workspace, usd, "10", date(2026, 11, 3), cat=travel, amount_primary="50")
    await data.txn(session, test_user, test_workspace, brl, "100", date(2026, 11, 4), cat=travel)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    nov = _category(report, "2026-11", "Travel")
    assert (nov.budget, nov.posted, nov.remaining) == (Decimal("500.00"), Decimal("150.00"), Decimal("350.00"))
    assert report.meta.currency == "BRL"


@pytest.mark.asyncio
async def test_accrual_card_purchase_consumes_next_months_budget(session, test_user, test_workspace, today):
    card = await data.account(
        session, test_user, test_workspace, "Card", acct_type="credit_card",
        statement_close_day=15, payment_due_day=10,
    )
    await data.account(session, test_user, test_workspace)
    shopping = await data.category(session, test_user, test_workspace, "Shopping")
    await data.budget(session, test_user, test_workspace, shopping, 300)
    await data.txn(
        session, test_user, test_workspace, card, "120", date(2026, 11, 18), cat=shopping,
        effective_date=date(2026, 12, 10),
    )

    cash = await _report(session, test_workspace, test_user, budget_forecast=True)
    assert _category(cash, "2026-11", "Shopping").posted == Decimal("120.00")
    assert _category(cash, "2026-12", "Shopping").remaining == Decimal("300.00")

    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
    accrual = await _report(session, test_workspace, test_user, budget_forecast=True)
    assert _category(accrual, "2026-11", "Shopping").remaining == Decimal("300.00")
    dec = _category(accrual, "2026-12", "Shopping")
    assert dec.scheduled == Decimal("120.00") and dec.remaining == Decimal("180.00")


@pytest.mark.asyncio
async def test_recurring_budget_and_month_override(session, test_user, test_workspace, today):
    await data.account(session, test_user, test_workspace)
    gifts = await data.category(session, test_user, test_workspace, "Gifts")
    await data.budget(session, test_user, test_workspace, gifts, 100, month=date(2026, 10, 1))
    await data.budget(session, test_user, test_workspace, gifts, 400, month=date(2026, 12, 1), recurring=False)
    once = await data.category(session, test_user, test_workspace, "One-off")
    await data.budget(session, test_user, test_workspace, once, 70, month=date(2026, 11, 1), recurring=False)
    report = await _report(session, test_workspace, test_user, months=2, budget_forecast=True)

    budgets = {
        (m.month, c.name): c.budget for m in report.budget_forecast.months for c in m.categories
    }
    assert budgets == {
        ("2026-11", "Gifts"): Decimal("100.00"),
        ("2026-11", "One-off"): Decimal("70.00"),
        ("2026-12", "Gifts"): Decimal("400.00"),
        ("2027-01", "Gifts"): Decimal("100.00"),
    }


@pytest.mark.asyncio
async def test_budgets_that_can_never_be_spent_are_skipped(session, test_user, test_workspace, today):
    await data.account(session, test_user, test_workspace)
    ignored = await data.category(session, test_user, test_workspace, "Ignored", is_ignored=True)
    savings = await data.category(session, test_user, test_workspace, "Savings move", treat_as_transfer=True)
    await data.budget(session, test_user, test_workspace, ignored, 100)
    await data.budget(session, test_user, test_workspace, savings, 100)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    assert all(not m.categories for m in report.budget_forecast.months)
    assert report.budget_forecast.total == Decimal("0.00")


@pytest.mark.asyncio
async def test_last_day_of_month_projects_nothing_for_it(session, test_user, test_workspace, monkeypatch):
    data.pin_today(monkeypatch, date(2026, 11, 30))
    await _worked_example(session, test_user, test_workspace)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    nov = report.budget_forecast.months[0]
    assert nov.days == 0 and nov.remaining == Decimal("110.00") and nov.forecast == Decimal("0.00")
    assert report.budget_forecast.total == Decimal("850.00")


@pytest.mark.asyncio
async def test_leap_february_at_the_end_of_the_horizon(session, test_user, test_workspace, monkeypatch):
    data.pin_today(monkeypatch, date(2027, 12, 31))
    await data.account(session, test_user, test_workspace)
    pharmacy = await data.category(session, test_user, test_workspace, "Pharmacy")
    await data.budget(session, test_user, test_workspace, pharmacy, 29, month=date(2027, 12, 1))
    report = await _report(session, test_workspace, test_user, months=2, budget_forecast=True)

    assert [m.month for m in report.budget_forecast.months] == ["2027-12", "2028-01", "2028-02"]
    feb = report.budget_forecast.months[-1]
    assert feb.days == 29 and feb.spread_to == "2028-02-29"
    assert _point(report, "2028-02-29").breakdowns["budget_outflow"] == 1.0


@pytest.mark.asyncio
async def test_24_month_horizon(session, test_user, test_workspace, today):
    await _worked_example(session, test_user, test_workspace)
    report = await _report(session, test_workspace, test_user, months=24, interval="monthly", budget_forecast=True)

    assert len(report.budget_forecast.months) == 25
    assert report.budget_forecast.months[-1].month == "2028-11"
    # 110 this month, then 850 for each of 24 months (streaming is scheduled every month).
    assert report.budget_forecast.total == Decimal("110.00") + Decimal("850.00") * 24
    assert round(sum(p.breakdowns["budget_outflow"] for p in report.trend), 2) == float(report.budget_forecast.total)


@pytest.mark.asyncio
async def test_current_month_agrees_with_the_budgets_page(session, test_user, test_workspace, today):
    await _worked_example(session, test_user, test_workspace)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)
    page = await budget_service.get_budget_rollover_summary(session, test_workspace.id, test_user.id, today)

    for row in page["categories"]:
        forecast = _category(report, "2026-11", row["category_name"])
        assert forecast.budget == row["budgeted"]
        assert forecast.posted == row["actual_spent"]
        assert forecast.posted + forecast.scheduled == row["projected_spent"]
        assert forecast.remaining == max(row["remaining"], Decimal("0"))


@pytest.mark.asyncio
async def test_other_workspaces_budgets_are_ignored(session, test_user, test_workspace, today):
    from app.models.workspace import Workspace

    await _worked_example(session, test_user, test_workspace)
    other = Workspace(name="Other synthetic workspace", created_by_user_id=test_user.id)
    await data.add(session, other)
    stray = await data.category(session, test_user, other, "Stray")
    await data.budget(session, test_user, other, stray, 999)
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    names = {c.name for m in report.budget_forecast.months for c in m.categories}
    assert "Stray" not in names
    assert report.budget_forecast.total == Decimal("960.00")


@pytest.mark.asyncio
async def test_closing_an_account_does_not_change_the_budget_layer(session, test_user, test_workspace, today):
    await _worked_example(session, test_user, test_workspace)
    restaurants = (await session.execute(select(Category).where(Category.name == "Restaurants"))).scalar_one()
    old = await data.account(session, test_user, test_workspace, "Old card")
    await data.txn(session, test_user, test_workspace, old, "30", date(2026, 11, 12), cat=restaurants)
    before = await _report(session, test_workspace, test_user, budget_forecast=True)
    old.is_closed = True
    await session.commit()
    after = await _report(session, test_workspace, test_user, budget_forecast=True)

    # Spending posted on it still used the budget, as on the Budgets page.
    assert _category(before, "2026-11", "Restaurants").posted == Decimal("130.00")
    assert after.budget_forecast == before.budget_forecast


@pytest.mark.asyncio
async def test_scheduled_refund_frees_budget_before_it_posts(session, test_user, test_workspace, today):
    """Q9 (issue #8): a scheduled credit in a budgeted category is negative scheduled spending, so the
    projected end balance does not move when the refund posts."""
    acct, restaurants, *_ = await _worked_example(session, test_user, test_workspace)
    refund = await data.txn(session, test_user, test_workspace, acct, "20", date(2026, 11, 25), "credit",
                            cat=restaurants, status="pending")
    pending = await _report(session, test_workspace, test_user, budget_forecast=True)

    nov = _category(pending, "2026-11", "Restaurants")
    assert (nov.posted, nov.scheduled, nov.remaining) == (Decimal("100.00"), Decimal("20.00"), Decimal("130.00"))

    refund.status, refund.date, refund.effective_date = "posted", today, today
    await session.commit()
    posted = await _report(session, test_workspace, test_user, budget_forecast=True)
    nov = _category(posted, "2026-11", "Restaurants")
    assert (nov.posted, nov.scheduled, nov.remaining) == (Decimal("80.00"), Decimal("40.00"), Decimal("130.00"))
    assert _breakdown(posted, "endingBalance") == _breakdown(pending, "endingBalance")


@pytest.mark.asyncio
async def test_recurring_refund_frees_budget_in_each_month(session, test_user, test_workspace, today):
    acct, restaurants, *_ = await _worked_example(session, test_user, test_workspace)
    await data.recurring(session, test_user, test_workspace, acct, "15", date(2026, 11, 27), cat=restaurants,
                         txn_type="credit")
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    assert _category(report, "2026-11", "Restaurants").remaining == Decimal("125.00")
    # December has no spending: net spending never goes below zero, as with a posted refund.
    assert _category(report, "2026-12", "Restaurants").remaining == Decimal("250.00")


@pytest.mark.asyncio
async def test_paired_transfer_in_a_budgeted_category_is_not_spending(session, test_user, test_workspace, today):
    acct, restaurants, *_ = await _worked_example(session, test_user, test_workspace)
    savings = await data.account(session, test_user, test_workspace, "Savings", acct_type="savings")
    out_leg = await data.txn(session, test_user, test_workspace, acct, "70", date(2026, 11, 14), cat=restaurants)
    in_leg = await data.txn(session, test_user, test_workspace, savings, "70", date(2026, 11, 14), "credit")
    out_leg.transfer_pair_id, in_leg.transfer_pair_id = in_leg.id, out_leg.id
    await session.commit()
    report = await _report(session, test_workspace, test_user, budget_forecast=True)

    assert _category(report, "2026-11", "Restaurants").posted == Decimal("100.00")
    assert report.budget_forecast.total == Decimal("960.00")


@pytest.mark.asyncio
async def test_future_dated_posted_installment_counts_once(session, test_user, test_workspace, today):
    acct, restaurants, *_ = await _worked_example(session, test_user, test_workspace)
    await data.txn(session, test_user, test_workspace, acct, "50", date(2026, 12, 5), cat=restaurants)
    off = await _report(session, test_workspace, test_user)
    on = await _report(session, test_workspace, test_user, budget_forecast=True)

    dec = _category(on, "2026-12", "Restaurants")
    assert (dec.scheduled, dec.remaining) == (Decimal("50.00"), Decimal("200.00"))
    assert _category(on, "2026-11", "Restaurants").remaining == Decimal("110.00")
    # The walk applies the 50 once (it is in both reports) and the budget adds only the rest.
    assert round(_breakdown(off, "endingBalance") - _breakdown(on, "endingBalance"), 2) == 910.0


@pytest.mark.asyncio
async def test_composition_splits_the_budget_figure_by_category(session, test_user, test_workspace, today):
    acct, *_ = await _worked_example(session, test_user, test_workspace)
    off = await _report(session, test_workspace, test_user)
    on = await _report(session, test_workspace, test_user, budget_forecast=True)

    budget_items = [c for c in on.composition if c.group == "budgetExpenses"]
    # Groceries: 0 left in November, 600 in December; Restaurants 110 + 250; Streaming is fully scheduled.
    assert [(c.label, c.value, c.color) for c in budget_items] == [
        ("Groceries", 600.0, "#F59E0B"), ("Restaurants", 360.0, "#FBBF24"),
    ]
    assert round(sum(c.value for c in budget_items), 2) == _breakdown(on, "budgetExpenses")
    # Upstream's items are untouched and come first.
    assert on.composition[: len(off.composition)] == off.composition

    filtered = await _report(session, test_workspace, test_user, account_ids=[acct.id], budget_forecast=True)
    assert {c.group for c in filtered.composition if c.key.startswith("budget:")} == {"unallocatedBudget"}
