"""`GET /api/accounts/available-balance` (F-001).

Mounted before upstream's accounts router: its `/{account_id}` route would
otherwise take this path and refuse it as a malformed id.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.fork.available_balance_service import get_available_balance

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AvailableBalanceAccount(BaseModel):
    id: uuid.UUID
    name: str
    display_name: Optional[str] = None
    type: str
    currency: str
    current_balance: float
    balance_primary: Optional[float] = None
    fx_fallback: bool = False


class AvailableBalanceRead(BaseModel):
    primary_currency: str
    total_primary: float
    by_currency: dict[str, float]
    fx_fallback: bool
    unconverted_currencies: list[str]
    accounts: list[AvailableBalanceAccount]
    excluded_accounts: list[AvailableBalanceAccount]


def _account(acc: dict[str, Any]) -> AvailableBalanceAccount:
    return AvailableBalanceAccount(
        id=acc["id"],
        name=acc["name"],
        display_name=acc.get("display_name"),
        type=acc["type"],
        currency=acc["currency"],
        current_balance=acc["current_balance"],
        balance_primary=acc.get("balance_primary"),
        fx_fallback=bool(acc.get("fx_fallback")),
    )


@router.get("/available-balance", response_model=AvailableBalanceRead)
async def read_available_balance(
    account_ids: Optional[list[uuid.UUID]] = Query(
        None, description="Limit to these accounts (an active collection); omitted = all"
    ),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    result = await get_available_balance(
        session, ctx.workspace.id, ctx.user.primary_currency, account_ids
    )
    return AvailableBalanceRead(
        primary_currency=result.primary_currency,
        total_primary=float(result.total_primary),
        by_currency={code: float(amount) for code, amount in result.by_currency.items()},
        fx_fallback=result.fx_fallback,
        unconverted_currencies=result.unconverted_currencies,
        accounts=[_account(dict(acc)) for acc in result.included],
        excluded_accounts=[_account(dict(acc)) for acc in result.excluded],
    )
