"""Budget-aware Cash Flow mode (F-002, issue #5): the database side.

Budget, posted and projected amounts come from
``budget_service.get_budget_vs_actual``, the numbers the Budgets page shows,
so the report and that page cannot disagree. The arithmetic is in
``app.fork.budget_forecast``.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.budget_forecast import (
    ZERO,
    BudgetForecastPlan,
    CategoryInput,
    horizon_months,
    plan_horizon,
)
from app.fork.budget_forecast_schemas import (
    BudgetForecastCategory,
    BudgetForecastExplanation,
    BudgetForecastMonth,
    CashFlowBudgetResponse,
    Placement,
)
from app.models.category import Category
from app.schemas.report import ReportBreakdown, ReportResponse
from app.services import budget_service

BUDGET_OUTFLOW_KEY = "budget_outflow"
_BREAKDOWN = {
    "subtracted": ("budgetExpenses", "Budgeted Spending"),
    "unallocated": ("unallocatedBudget", "Unallocated Budget"),
}
_BREAKDOWN_COLOR = "#F59E0B"


async def _never_spending_category_ids(session: AsyncSession, workspace_id: uuid.UUID) -> set[str]:
    # Spending in these categories never counts (`counts_as_pnl`), so a budget
    # on one could never be consumed and would read as money still to spend.
    result = await session.execute(
        select(Category.id).where(
            Category.workspace_id == workspace_id,
            or_(Category.is_ignored.is_(True), Category.treat_as_transfer.is_(True)),
        )
    )
    return {str(category_id) for category_id in result.scalars().all()}


async def build_budget_forecast(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    today: date,
    horizon_end: date,
) -> BudgetForecastPlan:
    skipped = await _never_spending_category_ids(session, workspace_id)
    months: list[tuple[date, list[CategoryInput]]] = []
    for month_start in horizon_months(today, horizon_end):
        rows = await budget_service.get_budget_vs_actual(session, workspace_id, user_id, month_start)
        items = [
            CategoryInput(
                category_id=str(row.category_id),
                name=row.category_name,
                color=row.category_color,
                icon=row.category_icon,
                group_id=str(row.group_id) if row.group_id else None,
                group_name=row.group_name,
                budget=row.budget_amount,
                posted=row.actual_amount,
                projected=row.projected_amount,
            )
            for row in rows
            if row.budget_amount is not None and str(row.category_id) not in skipped
        ]
        items.sort(key=lambda item: (item.group_name or "", item.name, item.category_id))
        months.append((month_start, items))
    return plan_horizon(months, today, horizon_end)


def explanation(plan: BudgetForecastPlan, placement: Placement) -> BudgetForecastExplanation:
    months = []
    for month in plan.months:
        months.append(BudgetForecastMonth(
            month=f"{month.month.year}-{month.month.month:02d}",
            spread_from=month.days[0].isoformat() if month.days else None,
            spread_to=month.days[-1].isoformat() if month.days else None,
            days=len(month.days),
            budget=month.budget,
            posted=month.posted,
            scheduled=month.scheduled,
            remaining=month.remaining,
            forecast=month.forecast,
            categories=[
                BudgetForecastCategory(
                    category_id=c.source.category_id,
                    name=c.source.name,
                    color=c.source.color,
                    icon=c.source.icon,
                    group_id=c.source.group_id,
                    group_name=c.source.group_name,
                    budget=c.budget,
                    posted=c.posted,
                    scheduled=c.scheduled,
                    remaining=c.remaining,
                    forecast=c.forecast,
                )
                for c in month.categories
            ],
        ))
    return BudgetForecastExplanation(placement=placement, total=plan.total, months=months)


def attach_budget_forecast(
    response: ReportResponse,
    plan: BudgetForecastPlan,
    subtracted: bool,
    interval: str,
) -> CashFlowBudgetResponse:
    """Add the per-point figure, the summary figure and the explanation.

    The balance itself is already right: when the placement is ``subtracted``
    the report walked the daily amounts into its balance flows. A filtered view
    gets no per-point figure: a point's flows net to the filtered balance's
    move, and the budget is not part of it (Q5).
    """
    from app.services.report_service import _format_date_label

    placement: Placement = "subtracted" if subtracted else "unallocated"

    by_label: dict[str, Decimal] = {}
    for day, amount in plan.daily.items():
        label = _format_date_label(day, interval)
        by_label[label] = by_label.get(label, ZERO) + amount

    trend = list(response.trend)
    if subtracted:
        trend = []
        for point in response.trend:
            breakdowns = dict(point.breakdowns)
            breakdowns[BUDGET_OUTFLOW_KEY] = round(float(by_label.get(point.date, ZERO)), 2)
            trend.append(point.model_copy(update={"breakdowns": breakdowns}))

    key, label = _BREAKDOWN[placement]
    breakdowns = list(response.summary.breakdowns)
    figure = ReportBreakdown(key=key, label=label, value=round(float(plan.total), 2), color=_BREAKDOWN_COLOR)
    ending = next((i for i, b in enumerate(breakdowns) if b.key == "endingBalance"), len(breakdowns))
    breakdowns.insert(ending, figure)
    summary = response.summary.model_copy(update={"breakdowns": breakdowns})

    return CashFlowBudgetResponse(
        **response.model_dump(exclude={"trend", "summary"}),
        trend=trend,
        summary=summary,
        budget_forecast=explanation(plan, placement),
    )
