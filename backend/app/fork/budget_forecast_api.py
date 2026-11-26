"""Request checks for the budget-aware Cash Flow mode (F-002, issue #5)."""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.core.access_token_scopes import grants

BASELINE_CONFLICT = "budget_forecast cannot be combined with baseline yet."


def guard_budget_forecast(request: Request, baseline: bool) -> None:
    """Refuse what the mode does not support, before any work is done.

    The mode reads budget limits, so an access token needs `budgets:read` as
    well as the `reports:read` the route already asked for.
    """
    if baseline:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=BASELINE_CONFLICT)
    token = getattr(request.state, "access_token", None)
    if token is not None and not grants(token.scopes, "budgets:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This access token's scopes don't cover this request.",
        )
