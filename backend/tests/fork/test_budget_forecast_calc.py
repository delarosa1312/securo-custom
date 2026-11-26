"""Pure calculator of the budget-aware Cash Flow mode (F-002). Synthetic amounts only."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.fork.budget_forecast import (
    CategoryInput,
    horizon_months,
    month_end,
    plan_category,
    plan_horizon,
    spread_days,
    spread_evenly,
)


def _item(budget, posted="0", projected=None, name="Restaurants", category_id="c1"):
    return CategoryInput(
        category_id=category_id,
        name=name,
        budget=Decimal(budget),
        posted=Decimal(posted),
        projected=Decimal(projected if projected is not None else posted),
    )


def test_worked_example_current_month():
    """Budget 250, posted 100, scheduled 40, on the 20th of a 30-day month: 110 over 10 days."""
    today = date(2026, 11, 20)
    days = spread_days(date(2026, 11, 1), today, date(2027, 5, 31))
    assert days[0] == date(2026, 11, 21) and days[-1] == date(2026, 11, 30) and len(days) == 10
    plan, parts = plan_category(_item("250", "100", "140"), days)
    assert (plan.budget, plan.posted, plan.scheduled, plan.remaining) == (
        Decimal("250.00"), Decimal("100.00"), Decimal("40.00"), Decimal("110.00")
    )
    assert plan.forecast == Decimal("110.00")
    assert {amount for _, amount in parts} == {Decimal("11.00")}


def test_more_posted_spending_lowers_the_remaining():
    """30 more spent on the 21st: 80 left over 22 to 30, cents to the earliest days."""
    days = spread_days(date(2026, 11, 1), date(2026, 11, 21), date(2026, 12, 31))
    plan, parts = plan_category(_item("250", "130", "170"), days)
    assert plan.remaining == Decimal("80.00")
    assert [a for _, a in parts] == [Decimal("8.89")] * 8 + [Decimal("8.88")]


@pytest.mark.parametrize(
    "amount, ndays, first, last",
    [
        ("250.00", 31, "8.07", "8.06"),
        ("0.05", 3, "0.02", "0.01"),
        ("0.01", 31, "0.01", "0.00"),
        ("1800.00", 30, "60.00", "60.00"),
        ("999999999.99", 29, "34482758.63", "34482758.62"),
    ],
)
def test_spread_adds_up_to_the_cent(amount, ndays, first, last):
    days = [date(2027, 1, 1) + timedelta(days=i) for i in range(ndays)]
    parts = spread_evenly(Decimal(amount), days)
    total = sum((a for _, a in parts), Decimal("0"))
    assert total == Decimal(amount)
    nonzero = [a for _, a in parts if a]
    assert nonzero[0] == Decimal(first)
    if Decimal(last):
        assert parts[-1][1] == Decimal(last)
    # Never more than one cent apart, and the larger parts come first.
    amounts = [a for _, a in parts]
    assert max(amounts) - min(amounts) <= Decimal("0.01")
    assert amounts == sorted(amounts, reverse=True)


def test_december_spread_from_the_plan():
    days = spread_days(date(2026, 12, 1), date(2026, 11, 20), date(2027, 5, 31))
    parts = spread_evenly(Decimal("250"), days)
    assert [a for _, a in parts[:14]] == [Decimal("8.07")] * 14
    assert [a for _, a in parts[14:]] == [Decimal("8.06")] * 17


def test_rounds_inputs_to_cents_and_keeps_the_identity():
    plan, parts = plan_category(_item("100.005", "33.333", "66.667"), spread_days(
        date(2027, 2, 1), date(2027, 1, 10), date(2027, 2, 28)
    ))
    assert plan.budget - plan.posted - plan.scheduled == plan.remaining
    assert sum((a for _, a in parts), Decimal("0")) == plan.remaining


def test_over_budget_and_exact_budget_floor_at_zero():
    days = spread_days(date(2027, 1, 1), date(2026, 12, 15), date(2027, 1, 31))
    over, parts = plan_category(_item("600", "650", "650"), days)
    assert over.remaining == Decimal("0.00") and parts == []
    exact, parts = plan_category(_item("30", "0", "30"), days)
    assert exact.scheduled == Decimal("30.00") and exact.remaining == Decimal("0.00") and parts == []
    zero_budget, parts = plan_category(_item("0"), days)
    assert zero_budget.remaining == Decimal("0.00") and parts == []


def test_scheduled_plus_budget_never_exceeds_the_budget_unless_scheduled_alone_does():
    days = spread_days(date(2027, 3, 1), date(2027, 2, 1), date(2027, 3, 31))
    for posted, projected in [("0", "0"), ("10", "40"), ("0", "249.99"), ("0", "300")]:
        plan, _ = plan_category(_item("250", posted, projected), days)
        spending = plan.scheduled + plan.forecast
        assert spending <= max(Decimal("250.00"), plan.scheduled)


def test_last_day_of_month_has_no_days_left():
    assert spread_days(date(2026, 11, 1), date(2026, 11, 30), date(2027, 1, 31)) == []
    plan, parts = plan_category(_item("250", "100"), [])
    assert plan.remaining == Decimal("150.00") and plan.forecast == Decimal("0.00") and parts == []


def test_leap_february_and_year_change():
    feb = spread_days(date(2028, 2, 1), date(2028, 1, 31), date(2028, 3, 31))
    assert len(feb) == 29 and feb[-1] == date(2028, 2, 29)
    assert len(spread_days(date(2027, 2, 1), date(2027, 1, 31), date(2027, 3, 31))) == 28
    # Today is the 28th of a leap February: one day left.
    assert spread_days(date(2028, 2, 1), date(2028, 2, 28), date(2028, 3, 31)) == [date(2028, 2, 29)]
    assert horizon_months(date(2026, 12, 31), date(2027, 1, 31)) == [date(2026, 12, 1), date(2027, 1, 1)]
    assert month_end(date(2028, 2, 1)) == date(2028, 2, 29)


def test_horizon_of_24_months_sums_exactly():
    today = date(2026, 11, 20)
    end = month_end(date(2028, 11, 1))
    months = horizon_months(today, end)
    assert len(months) == 25 and months[-1] == date(2028, 11, 1)
    plan = plan_horizon(
        [(m, [_item("250"), _item("99.99", category_id="c2", name="Pharmacy")]) for m in months],
        today,
        end,
    )
    # Current month: 10 days left of 250 and 99.99 in full; then 24 whole months.
    assert plan.total == Decimal("349.99") * 25
    assert sum(plan.daily.values(), Decimal("0")) == plan.total
    assert min(plan.daily) == date(2026, 11, 21) and max(plan.daily) == end
    assert len(plan.daily) == (end - today).days
    for month in plan.months:
        in_month = [a for d, a in plan.daily.items() if d.year == month.month.year and d.month == month.month.month]
        assert sum(in_month, Decimal("0")) == month.forecast


def test_months_are_independent():
    """A December budget never spills into January, and January's is spread over January."""
    today = date(2026, 12, 15)
    plan = plan_horizon(
        [
            (date(2026, 12, 1), [_item("160")]),
            (date(2027, 1, 1), [_item("310")]),
        ],
        today,
        date(2027, 1, 31),
    )
    assert sum(a for d, a in plan.daily.items() if d.month == 12) == Decimal("160.00")
    assert sum(a for d, a in plan.daily.items() if d.month == 1) == Decimal("310.00")
    assert plan.daily[date(2027, 1, 1)] == Decimal("10.00")
    assert plan.daily[date(2026, 12, 16)] == Decimal("10.00")
