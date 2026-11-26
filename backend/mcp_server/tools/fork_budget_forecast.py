"""The budget-aware option of `get_cash_flow` (F-002, issue #5). Registers no tool of its own."""
from __future__ import annotations

from typing import Any

BUDGET_FORECAST_PARAMETER: dict[str, Any] = {
    "type": "boolean",
    "default": False,
    "description": (
        "Also count what is left of each monthly budget as expected spending: limit minus posted minus "
        "scheduled spending, never below zero, spread evenly per day (this month from tomorrow, later "
        "months over all their days) and subtracted from the projected balance. Each trend point gains "
        "`budget_outflow`, the summary gains `budgetExpenses`, and `budget_forecast` explains every month "
        "and category (budget, posted, scheduled, remaining, forecast). Nothing is written."
    ),
}
