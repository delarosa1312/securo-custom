"""Fork F-004 MCP tools: recurring transfers and the conversion of existing scheduled transfers.

Cancelling (deactivate or delete) a transfer schedule is upstream's
`propose_cancel_recurring_transaction`; listing is upstream's `list_recurring_transactions`, which
shows the destination.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.fork import recurring_transfers, transfer_conversion
from app.fork.api.recurring_transfers import serialize
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services import recurring_transaction_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, parse_uuid, resolve_workspace_id
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply

_FREQUENCY = {"type": "string", "enum": ["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]}
_WEEKEND = {"type": "string", "enum": ["none", "previous_friday", "next_monday"]}


def _uuid_or_none(value: Any):
    try:
        return parse_uuid(value)
    except (ValueError, TypeError, AttributeError):
        return None


async def _account(session: AsyncSession, ws_id, account_id: Any) -> Account | None:
    account_uuid = _uuid_or_none(account_id)
    if account_uuid is None:
        return None
    return (
        await session.execute(
            select(Account).where(Account.id == account_uuid, Account.workspace_id == ws_id)
        )
    ).scalar_one_or_none()


def _name(account: Account | None) -> str | None:
    return (account.display_name or account.name) if account else None


def _amount(value: Any) -> Decimal:
    return Decimal(str(value))


@tool(
    name="propose_create_recurring_transfer",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for a recurring transfer between two of the user's own accounts "
        "(e.g. 'move 200 from checking to savings on the 10th of every month'). Each occurrence "
        "becomes one transfer pair: the source account goes down, the destination goes up, and "
        "it is never income or expense. Between accounts in different currencies pass "
        "destination_amount, the fixed amount received each time; without it the transfer is "
        "refused. Call list_accounts first for the account ids."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {"type": "string", "minLength": 1, "maxLength": 500},
            "amount": {"type": "number", "exclusiveMinimum": 0},
            "from_account_id": {"type": "string", "format": "uuid"},
            "to_account_id": {"type": "string", "format": "uuid"},
            "destination_amount": {"type": "number", "exclusiveMinimum": 0},
            "frequency": _FREQUENCY,
            "weekend_adjustment": {**_WEEKEND, "default": "none"},
            "day_of_month": {
                "type": "integer", "minimum": 1, "maximum": 31,
                "description": "Required for monthly, quarterly or semiannual",
            },
            "start_date": {"type": "string", "format": "date", "description": "Defaults to today"},
            "end_date": {"type": "string", "format": "date"},
            "apply": _APPLY_FIELD,
        },
        "required": ["description", "amount", "from_account_id", "to_account_id", "frequency"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_create_recurring_transfer(
    *,
    session: AsyncSession,
    ctx: CallContext,
    description: str,
    amount: float,
    from_account_id: str,
    to_account_id: str,
    frequency: str,
    destination_amount: float | None = None,
    weekend_adjustment: str = "none",
    day_of_month: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    if frequency in ("monthly", "quarterly", "semiannual") and not day_of_month:
        return {"error": "day_of_month is required for monthly, quarterly, or semiannual frequency"}
    ws_id = await resolve_workspace_id(session, ctx)
    source = await _account(session, ws_id, from_account_id)
    destination = await _account(session, ws_id, to_account_id)
    if source is None:
        return {"error": "source account not found"}
    if destination is None:
        return {"error": "destination account not found"}
    start = parse_date(start_date) if start_date else app_today()
    try:
        data = RecurringTransactionCreate.model_validate(
            {
                "description": description.strip(),
                "amount": _amount(amount),
                "currency": source.currency,
                "type": "debit",
                "frequency": frequency,
                "weekend_adjustment": weekend_adjustment,
                "day_of_month": int(day_of_month) if day_of_month else None,
                "start_date": start,
                "end_date": parse_date(end_date) if end_date else None,
                "account_id": source.id,
                "destination_account_id": destination.id,
                "destination_amount": _amount(destination_amount) if destination_amount is not None else None,
            }
        )
        await recurring_transfers.check_create(session, ws_id, data)
    except (ValueError, ValidationError) as exc:
        return {"error": str(exc)}
    preview = {
        "kind": "create_recurring_transfer",
        "proposed": {
            "description": data.description,
            "amount": float(data.amount),
            "currency": source.currency,
            "from_account_id": str(source.id),
            "from_account_name": _name(source),
            "to_account_id": str(destination.id),
            "to_account_name": _name(destination),
            "destination_amount": num(data.destination_amount),
            "destination_currency": destination.currency,
            "frequency": data.frequency,
            "weekend_adjustment": data.weekend_adjustment,
            "day_of_month": data.day_of_month,
            "start_date": data.start_date.isoformat(),
            "end_date": data.end_date.isoformat() if data.end_date else None,
        },
        "apply_endpoint": "POST /api/recurring-transactions",
    }
    if _can_apply(ctx, apply):
        try:
            created = await recurring_transaction_service.create_recurring_transaction(
                session, ws_id, ctx.user_id, data
            )
        except (ValueError, recurring_transaction_service.RecurringConflictError) as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True, "id": str(created.id)}
    return preview


@tool(
    name="propose_update_recurring_transfer",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for editing a recurring transfer (list_recurring_transactions shows "
        "transfers with a to_account_id): amount, accounts, the fixed amount received between "
        "currencies, schedule, end date, active. Pass only the fields to change. Already "
        "generated transfers are not changed. Use propose_cancel_recurring_transaction to "
        "deactivate or delete it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": {"type": "string", "format": "uuid"},
            "description": {"type": "string", "minLength": 1, "maxLength": 500},
            "amount": {"type": "number", "exclusiveMinimum": 0},
            "from_account_id": {"type": "string", "format": "uuid"},
            "to_account_id": {"type": "string", "format": "uuid"},
            "destination_amount": {"type": "number", "exclusiveMinimum": 0},
            "frequency": _FREQUENCY,
            "weekend_adjustment": _WEEKEND,
            "day_of_month": {"type": "integer", "minimum": 1, "maximum": 31},
            "end_date": {"type": "string", "format": "date"},
            "is_active": {"type": "boolean"},
            "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_update_recurring_transfer(
    *,
    session: AsyncSession,
    ctx: CallContext,
    recurring_id: str,
    description: str | None = None,
    amount: float | None = None,
    from_account_id: str | None = None,
    to_account_id: str | None = None,
    destination_amount: float | None = None,
    frequency: str | None = None,
    weekend_adjustment: str | None = None,
    day_of_month: int | None = None,
    end_date: str | None = None,
    is_active: bool | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec_uuid = _uuid_or_none(recurring_id)
    rec = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.id == rec_uuid, RecurringTransaction.workspace_id == ws_id
            )
        )
    ).scalar_one_or_none() if rec_uuid else None
    if rec is None or not recurring_transfers.is_transfer_schedule(rec):
        return {"error": "recurring transfer not found"}

    changes: dict[str, Any] = {}
    update: dict[str, Any] = {}
    if description is not None:
        changes["description"] = update["description"] = description.strip()
    if amount is not None:
        changes["amount"] = float(amount)
        update["amount"] = _amount(amount)
    for key, value in (("from_account_id", from_account_id), ("to_account_id", to_account_id)):
        if value is None:
            continue
        account = await _account(session, ws_id, value)
        if account is None:
            return {"error": f"{'source' if key == 'from_account_id' else 'destination'} account not found"}
        changes[key] = str(account.id)
        changes[key.replace("_id", "_name")] = _name(account)
        update["account_id" if key == "from_account_id" else "destination_account_id"] = account.id
        if key == "from_account_id":
            changes["currency"] = update["currency"] = account.currency
    if destination_amount is not None:
        changes["destination_amount"] = float(destination_amount)
        update["destination_amount"] = _amount(destination_amount)
    for key, value in (
        ("frequency", frequency), ("weekend_adjustment", weekend_adjustment),
        ("day_of_month", day_of_month), ("is_active", is_active),
    ):
        if value is not None:
            changes[key] = update[key] = value
    if end_date is not None:
        parsed = parse_date(end_date)
        changes["end_date"] = parsed.isoformat() if parsed else None
        update["end_date"] = parsed
    if not changes:
        return {"error": "no changes provided"}
    try:
        data = RecurringTransactionUpdate.model_validate(update)
        await recurring_transfers.check_update(
            session, ws_id, rec, data.model_dump(exclude_unset=True), None
        )
    except (ValueError, ValidationError) as exc:
        return {"error": str(exc)}

    preview = {
        "kind": "update_recurring_transfer",
        "target": {
            "id": str(rec.id),
            "description": rec.description,
            "amount": num(rec.amount),
            "currency": rec.currency,
            "from_account_id": str(rec.account_id),
            "to_account_id": str(rec.destination_account_id),
            "destination_amount": num(rec.destination_amount),
            "frequency": rec.frequency,
            "weekend_adjustment": rec.weekend_adjustment,
            "day_of_month": rec.day_of_month,
            "end_date": rec.end_date.isoformat() if rec.end_date else None,
            "is_active": bool(rec.is_active),
        },
        "changes": changes,
        "apply_endpoint": f"PATCH /api/recurring-transactions/{rec.id}",
    }
    if _can_apply(ctx, apply):
        try:
            updated = await recurring_transaction_service.update_recurring_transaction(
                session, rec.id, ws_id, data
            )
        except ValueError as exc:
            return {**preview, "error": str(exc)}
        if updated is None:
            return {**preview, "error": "recurring transfer not found"}
        return {**preview, "applied": True, "id": str(updated.id)}
    return preview


@tool(
    name="list_transfer_conversions",
    description=(
        "Read only. Future transfers between the user's accounts that were scheduled one by one "
        "and follow a rhythm (same accounts, amount and description, dates weekly to yearly), "
        "grouped as candidate series to become one recurring transfer each. Converting changes "
        "no balance or forecast: it only links those transfers to a new recurring transfer. "
        "Nothing is converted here; propose_convert_transfer_series converts one series after "
        "the user confirms."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    tags=["read", "recurring"],
)
async def list_transfer_conversions(*, session: AsyncSession, ctx: CallContext) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    result = await transfer_conversion.preview(session, ws_id)
    return {
        "series": [serialize(series).model_dump(mode="json") for series in result.series],
        "left_alone": result.left_alone,
    }


@tool(
    name="propose_convert_transfer_series",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for converting one series from list_transfer_conversions into a "
        "recurring transfer. By default the new schedule ends at the series' last transfer, so "
        "no number moves; pass open_ended=true (or an end_date) for it to keep going after that. "
        "One series per call, each confirmed by the user."
    ),
    parameters={
        "type": "object",
        "properties": {
            "series_id": {"type": "string", "format": "uuid"},
            "end_date": {"type": "string", "format": "date"},
            "open_ended": {"type": "boolean", "default": False},
            "apply": _APPLY_FIELD,
        },
        "required": ["series_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_convert_transfer_series(
    *,
    session: AsyncSession,
    ctx: CallContext,
    series_id: str,
    end_date: str | None = None,
    open_ended: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    series_uuid = _uuid_or_none(series_id)
    found = next(
        (s for s in (await transfer_conversion.preview(session, ws_id)).series if s.series_id == series_uuid),
        None,
    )
    if found is None:
        return {"error": transfer_conversion.STALE_SERIES}
    parsed_end = parse_date(end_date) if end_date else None
    if open_ended and parsed_end is not None:
        return {"error": "Give an end date or open_ended, not both"}
    if parsed_end is not None and parsed_end < found.last_nominal:
        return {"error": f"The end date cannot be before the series' last transfer ({found.last_nominal})"}
    series = serialize(found).model_dump(mode="json")
    preview = {
        "kind": "convert_transfer_series",
        "target": series,
        "proposed": {
            "end_date": None if open_ended else (parsed_end or found.last_nominal).isoformat(),
            "open_ended": bool(open_ended),
        },
        "apply_endpoint": f"POST /api/recurring-transactions/transfer-conversions/{found.series_id}/apply",
    }
    if _can_apply(ctx, apply):
        try:
            schedule, linked = await transfer_conversion.apply(
                session, ws_id, ctx.user_id, found.series_id, end_date=parsed_end, open_ended=open_ended
            )
        except (ValueError, transfer_conversion.ConversionConflict) as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True, "id": str(schedule.id), "linked_transfers": linked}
    return preview
