"""Request checks for the budget-aware Cash Flow mode (F-002, issue #5)."""
from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import Field, TypeAdapter, ValidationError

from app.core.access_token_scopes import grants

BASELINE_CONFLICT = "budget_forecast cannot be combined with baseline yet."
UPSTREAM_MAX_MONTHS = 12
MAX_MONTHS = 24

# Upstream's own `months` rule, so a request without the mode gets upstream's
# exact 422 body: the route accepts up to 24 only for the budget mode.
_upstream_months = TypeAdapter(Annotated[int, Field(ge=1, le=UPSTREAM_MAX_MONTHS)])


def guard_cash_flow_request(request: Request, baseline: bool, budget_forecast: bool) -> None:
    """Refuse what the request cannot have, before any work is done."""
    if not budget_forecast:
        raw = request.query_params.get("months")
        if raw is not None:
            try:
                _upstream_months.validate_python(raw)
            except ValidationError as exc:
                raise RequestValidationError(
                    [{**error, "loc": ("query", "months")} for error in exc.errors(include_url=False)]
                ) from None
        return
    if baseline:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=BASELINE_CONFLICT)
    # The mode reads budget limits, so an access token needs `budgets:read`
    # as well as the `reports:read` the route already asked for.
    token = getattr(request.state, "access_token", None)
    if token is not None and not grants(token.scopes, "budgets:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This access token's scopes don't cover this request.",
        )
