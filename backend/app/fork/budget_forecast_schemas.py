"""Response shapes of the budget-aware Cash Flow mode (F-002, issue #5)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.report import ReportResponse

# All accounts: the daily amounts are subtracted from the projected balance.
# Account or collection filter: budgets have no account, so the amount is
# reported apart and the filtered balance is left alone (ledger Q5).
Placement = Literal["subtracted", "unallocated"]


class BudgetForecastCategory(BaseModel):
    category_id: str
    name: str
    color: Optional[str] = None
    icon: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    budget: Decimal
    posted: Decimal
    scheduled: Decimal
    # Never below zero. `forecast` is the part spread over the horizon: it is
    # zero when no day of the month is left (the last day of a month).
    remaining: Decimal
    forecast: Decimal


class BudgetForecastMonth(BaseModel):
    month: str  # YYYY-MM
    spread_from: Optional[str] = None  # YYYY-MM-DD, first day the remaining is spent
    spread_to: Optional[str] = None
    days: int
    budget: Decimal
    posted: Decimal
    scheduled: Decimal
    remaining: Decimal
    forecast: Decimal
    categories: list[BudgetForecastCategory]


class BudgetForecastExplanation(BaseModel):
    placement: Placement
    rule: Literal["even_daily"] = "even_daily"
    total: Decimal
    months: list[BudgetForecastMonth]


class CashFlowBudgetResponse(ReportResponse):
    """The Cash Flow report with the budget mode on: upstream's fields plus the explanation."""

    budget_forecast: BudgetForecastExplanation
