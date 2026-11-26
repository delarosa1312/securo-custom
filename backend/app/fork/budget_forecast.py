"""Remaining monthly budgets as virtual daily spending (F-002, issue #5).

Pure: plain inputs in, exact ``Decimal`` amounts out, no database. The caller
supplies, per month and budgeted category, the limit and the posted and
projected (posted plus scheduled) spending, all in the report's currency, as
``budget_service.get_budget_vs_actual`` computes them for the Budgets page.

Rules (decided by Matheus, ledger Q4):
- remaining = limit - posted - scheduled, never below zero;
- the remaining of the current month is spread evenly over the days left,
  starting tomorrow; a future month's over all its days;
- cents are split deterministically: every day gets the same whole number of
  cents and the leftover cents go one each to the earliest days, so the days
  add up exactly to the remaining amount.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Sequence

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_cents(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def month_end(month_start: date) -> date:
    return month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])


def next_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def horizon_months(today: date, horizon_end: date) -> list[date]:
    """First day of every month from today's month through the horizon's last month."""
    months: list[date] = []
    cursor = today.replace(day=1)
    while cursor <= horizon_end:
        months.append(cursor)
        cursor = next_month(cursor)
    return months


def spread_days(month_start: date, today: date, horizon_end: date) -> list[date]:
    """Days of a month the remaining budget is spent on: after today, up to the horizon."""
    first = max(month_start, today + timedelta(days=1))
    last = min(month_end(month_start), horizon_end)
    if first > last:
        return []
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def spread_evenly(amount: Decimal, days: Sequence[date]) -> list[tuple[date, Decimal]]:
    """Split ``amount`` (to the cent) over ``days``; the parts add up exactly."""
    total_cents = int(to_cents(amount) / CENT)
    if total_cents <= 0 or not days:
        return []
    base, extra = divmod(total_cents, len(days))
    return [
        (day, Decimal(base + (1 if index < extra else 0)) * CENT)
        for index, day in enumerate(days)
    ]


@dataclass(frozen=True)
class CategoryInput:
    """One budgeted category in one month, amounts in the report's currency."""

    category_id: str
    name: str
    budget: Decimal
    posted: Decimal
    projected: Decimal
    color: Optional[str] = None
    icon: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None


@dataclass(frozen=True)
class CategoryPlan:
    source: CategoryInput
    budget: Decimal
    posted: Decimal
    scheduled: Decimal
    remaining: Decimal
    forecast: Decimal


@dataclass
class MonthPlan:
    month: date
    days: list[date]
    categories: list[CategoryPlan] = field(default_factory=list)

    @property
    def budget(self) -> Decimal:
        return sum((c.budget for c in self.categories), ZERO)

    @property
    def posted(self) -> Decimal:
        return sum((c.posted for c in self.categories), ZERO)

    @property
    def scheduled(self) -> Decimal:
        return sum((c.scheduled for c in self.categories), ZERO)

    @property
    def remaining(self) -> Decimal:
        return sum((c.remaining for c in self.categories), ZERO)

    @property
    def forecast(self) -> Decimal:
        return sum((c.forecast for c in self.categories), ZERO)


@dataclass
class BudgetForecastPlan:
    months: list[MonthPlan]
    daily: dict[date, Decimal]

    @property
    def total(self) -> Decimal:
        return sum((m.forecast for m in self.months), ZERO)


def plan_category(item: CategoryInput, days: Sequence[date]) -> tuple[CategoryPlan, list[tuple[date, Decimal]]]:
    # Rounded first, so budget - posted - scheduled = remaining holds to the cent
    # in the explanation whenever the remaining is not floored.
    budget = to_cents(item.budget)
    posted = to_cents(item.posted)
    projected = to_cents(item.projected)
    scheduled = projected - posted
    remaining = max(budget - projected, ZERO)
    parts = spread_evenly(remaining, days)
    forecast = sum((amount for _, amount in parts), ZERO)
    return (
        CategoryPlan(item, budget, posted, scheduled, remaining, forecast),
        parts,
    )


def plan_horizon(
    months: Sequence[tuple[date, Sequence[CategoryInput]]],
    today: date,
    horizon_end: date,
) -> BudgetForecastPlan:
    """Plan every month: remaining per category and the dated daily outflows."""
    plans: list[MonthPlan] = []
    daily: dict[date, Decimal] = {}
    for month_start, items in months:
        days = spread_days(month_start, today, horizon_end)
        month_plan = MonthPlan(month=month_start, days=days)
        for item in items:
            category_plan, parts = plan_category(item, days)
            month_plan.categories.append(category_plan)
            for day, amount in parts:
                daily[day] = daily.get(day, ZERO) + amount
        plans.append(month_plan)
    return BudgetForecastPlan(months=plans, daily=daily)
