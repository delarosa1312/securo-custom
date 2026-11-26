"""Fork F-001 MCP tools: read the available balance, propose the per-account setting."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.available_balance import AVAILABLE_ACCOUNT_TYPES
from app.fork.available_balance_service import get_available_balance as load_available_balance
from app.models.user import User
from app.schemas.account import AccountUpdate
from app.services import account_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_uuid, resolve_workspace_id
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


def _uuid_or_none(value: Any):
    # A malformed id names no account; answer like one that does not exist.
    try:
        return parse_uuid(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _account_row(acc: Any) -> dict[str, Any]:
    return {
        "id": str(acc["id"]),
        "name": acc.get("display_name") or acc["name"],
        "type": acc["type"],
        "currency": acc["currency"],
        "current_balance": num(acc.get("current_balance")),
        "balance_primary": num(acc.get("balance_primary")),
    }


@tool(
    name="get_available_balance",
    description=(
        "The money available to spend now: open checking, savings and wallet "
        "accounts whose include_in_available setting is on, in the user's "
        "primary currency and per currency. Accounts switched off (reserved "
        "money, such as an emergency fund) are listed apart and still count in "
        "total balance, net worth and reports. Pass account_ids to limit it to "
        "a collection's accounts."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
            },
        },
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def get_available_balance(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_ids: list[str] | None = None,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    user = await session.get(User, ctx.user_id)
    if user is None:
        return {"error": "user not found"}
    # An empty list is a collection with no accounts: nothing, not everything.
    ids = (
        [u for value in account_ids if (u := _uuid_or_none(value)) is not None]
        if account_ids is not None
        else None
    )
    result = await load_available_balance(session, ws_id, user.primary_currency, ids)
    return {
        "primary_currency": result.primary_currency,
        "available_balance": num(result.total_primary),
        "by_currency": {code: num(amount) for code, amount in result.by_currency.items()},
        "fx_fallback": result.fx_fallback,
        "unconverted_currencies": result.unconverted_currencies,
        "accounts": [_account_row(acc) for acc in result.included],
        "excluded_accounts": [_account_row(acc) for acc in result.excluded],
    }


@tool(
    name="propose_set_account_available",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for including an account in, or leaving it out of, "
        "the available balance. Leaving it out keeps its balance, history, "
        "reports and net worth; only available-money figures (the dashboard's "
        "Available balance, the card widget's after-paying figure, the "
        "predictive forecast's starting balance and runway) stop counting it. "
        "Works for manual and bank-connected checking, savings and wallet "
        "accounts. Call list_accounts first to find the account_id."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "include_in_available": {"type": "boolean"},
            "apply": _APPLY_FIELD,
        },
        "required": ["account_id", "include_in_available"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "accounts"],
)
async def propose_set_account_available(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_id: str,
    include_in_available: bool,
    apply: bool = False,
) -> dict[str, Any]:
    if not isinstance(include_in_available, bool):
        return {"error": "include_in_available must be true or false"}
    ws_id = await resolve_workspace_id(session, ctx)
    account_uuid = _uuid_or_none(account_id)
    if account_uuid is None:
        return {"error": "account not found"}
    account = await account_service.get_account_with_balances(session, account_uuid, ws_id)
    if account is None:
        return {"error": "account not found"}
    if account["is_closed"]:
        return {"error": "closed accounts are never part of the available balance"}
    if account["type"] not in AVAILABLE_ACCOUNT_TYPES:
        return {
            "error": (
                f"{account['type']} accounts are never part of the available balance; "
                "only checking, savings and wallet accounts have this setting"
            )
        }
    if account["include_in_available"] == include_in_available:
        return {"error": "nothing to change"}

    preview = {
        "kind": "set_account_available",
        "target": {
            **_account_row(account),
            "include_in_available": account["include_in_available"],
        },
        "changes": {"include_in_available": include_in_available},
        "apply_endpoint": f"PATCH /api/accounts/{account_uuid}",
    }

    if _can_apply(ctx, apply):
        try:
            updated = await account_service.update_account(
                session,
                account_uuid,
                ws_id,
                AccountUpdate(include_in_available=include_in_available),
            )
        except ValueError as exc:
            return {**preview, "error": str(exc)}
        if updated is None:
            return {**preview, "error": "account not found"}
        return {**preview, "applied": True, "id": str(updated.id)}

    return preview
