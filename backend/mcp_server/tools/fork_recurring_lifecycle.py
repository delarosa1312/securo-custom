"""Fork F-005 MCP tools: recurring occurrences one at a time and the whole series' lifecycle.

Reads: `list_recurring_occurrences`, `list_overdue_occurrences`. Writes are proposals (preview first,
`apply=true` from an external client after the user confirms), each the same service as its REST
route. Listing schedules by status is upstream's `list_recurring_transactions` (fork `status`).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.fork import recurring_lifecycle as lifecycle
from app.fork import recurring_series as series
from app.fork.api.recurring_lifecycle import impact_read
from app.models.recurring_transaction import RecurringTransaction
from app.schemas.recurring_transaction import RecurringTransactionUpdate
from app.services.recurring_transaction_service import RecurringConflictError
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, parse_uuid, resolve_workspace_id
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply

_ERRORS = (lifecycle.LifecycleNotFound, lifecycle.LifecycleConflict, RecurringConflictError, ValueError)
_ID = {"type": "string", "format": "uuid"}
_DATE = {"type": "string", "format": "date"}


def _uuid(value: Any):
    try:
        return parse_uuid(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _date(value: Any) -> Optional[date]:
    try:
        return parse_date(value)
    except (ValueError, TypeError):
        return None


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Not an amount: {value}")


async def _schedule(session: AsyncSession, ws_id, recurring_id: Any) -> Optional[RecurringTransaction]:
    rid = _uuid(recurring_id)
    if rid is None:
        return None
    return (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.id == rid, RecurringTransaction.workspace_id == ws_id
            )
        )
    ).scalar_one_or_none()


def _target(rec: RecurringTransaction) -> dict[str, Any]:
    return {
        "id": str(rec.id),
        "description": rec.description,
        "amount": num(rec.amount),
        "currency": rec.currency,
        "type": rec.type,
        "frequency": rec.frequency,
        "next_occurrence": rec.next_occurrence.isoformat(),
        "end_date": rec.end_date.isoformat() if rec.end_date else None,
        "is_active": bool(rec.is_active),
        "deleted": rec.deleted_at is not None,
        "to_account_id": str(rec.destination_account_id) if rec.destination_account_id else None,
    }


def _occurrence(item: lifecycle.Occurrence) -> dict[str, Any]:
    return {
        "nominal_date": item.nominal_date.isoformat(),
        "date": item.date.isoformat(),
        "amount": num(item.amount),
        "destination_amount": num(item.destination_amount),
        "state": item.state,
        "transaction_id": str(item.transaction_id) if item.transaction_id else None,
    }


async def _state(session: AsyncSession, rec: RecurringTransaction, nominal: date) -> Optional[lifecycle.Occurrence]:
    found = await lifecycle.list_occurrences(session, rec, nominal, nominal + timedelta(days=1), app_today())
    return found[0] if found else None


@tool(
    name="list_recurring_occurrences",
    description=(
        "Read only. The occurrences of one recurring transaction with their state: recorded (a "
        "transaction), edited (recorded ahead with its own amount or date), skipped, removed "
        "(recorded, then its transaction was deleted), scheduled, overdue (dated today or earlier, "
        "not recorded: counts in no total until recorded) or past. Default window: three months "
        "back to a year ahead, at most 60. Use nominal_date to act on one occurrence."
    ),
    parameters={
        "type": "object",
        "properties": {"recurring_id": _ID, "from_date": _DATE, "to_date": _DATE},
        "required": ["recurring_id"],
        "additionalProperties": False,
    },
    tags=["read", "recurring"],
)
async def list_recurring_occurrences(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str,
    from_date: str | None = None, to_date: str | None = None,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    if rec is None:
        return {"error": lifecycle.NOT_FOUND}
    today = app_today()
    start = _date(from_date) or today - timedelta(days=92)
    end = (_date(to_date) or today + timedelta(days=366)) + timedelta(days=1)
    items = await lifecycle.list_occurrences(session, rec, start, end, today)
    return {"recurring": _target(rec), "items": [_occurrence(item) for item in items]}


@tool(
    name="list_overdue_occurrences",
    description=(
        "Read only. Recurring occurrences dated today or earlier that are not transactions yet "
        "(typically automatic generation is off). They count in no balance, total or budget until "
        "recorded. Each can be recorded (propose_record_occurrence), skipped "
        "(propose_skip_occurrence) or matched to an existing transaction (propose_link_occurrence)."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    tags=["read", "recurring"],
)
async def list_overdue_occurrences(*, session: AsyncSession, ctx: CallContext) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    found = await lifecycle.overdue(session, ws_id, app_today())
    items = [
        {
            "recurring_id": str(item.schedule.id),
            "description": item.schedule.description,
            "amount": num(item.schedule.amount),
            "currency": item.schedule.currency,
            "type": item.schedule.type,
            "nominal_date": item.nominal_date.isoformat(),
            "date": item.date.isoformat(),
            "auto_generate": bool(item.schedule.auto_generate),
        }
        for item in found
    ]
    return {"items": items, "total": len(items)}


@tool(
    name="propose_skip_occurrence",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for skipping one occurrence of a recurring transaction (it is never "
        "recorded and leaves every forecast), or, with undo=true, for undoing a skip. The rest of "
        "the series does not change. nominal_date comes from list_recurring_occurrences."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": _ID, "nominal_date": _DATE,
            "undo": {"type": "boolean", "default": False}, "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id", "nominal_date"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_skip_occurrence(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, nominal_date: str,
    undo: bool = False, apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    nominal = _date(nominal_date)
    if rec is None or nominal is None:
        return {"error": lifecycle.NOT_FOUND if rec is None else "nominal_date is not a date"}
    current = await _state(session, rec, nominal)
    if current is None or current.nominal_date != nominal:
        return {"error": lifecycle.NOT_AN_OCCURRENCE}
    if undo and current.state != "skipped":
        return {"error": lifecycle.NOT_SKIPPED}
    if not undo and current.state not in ("scheduled", "overdue"):
        return {"error": lifecycle.ALREADY_HANDLED}
    preview = {
        "kind": "unskip_occurrence" if undo else "skip_occurrence",
        "target": {**_target(rec), "occurrence": _occurrence(current)},
        "changes": {"state": {"from": current.state, "to": "scheduled" if undo else "skipped"}},
        "apply_endpoint": f"{'DELETE' if undo else 'POST'} /api/recurring-transactions/{rec.id}/occurrences/{nominal}/skip",
    }
    if _can_apply(ctx, apply):
        try:
            if undo:
                await lifecycle.unskip(session, ws_id, rec.id, nominal)
            else:
                await lifecycle.skip(session, ws_id, rec.id, nominal)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True}
    return preview


@tool(
    name="propose_record_occurrence",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for recording one occurrence of a recurring transaction now, as a "
        "transaction: as scheduled (e.g. an overdue bill) or edited, with its own amount and/or "
        "date (e.g. 'January rent is 1550 on the 12th'). Only that occurrence changes; the series "
        "keeps its values. A transfer between currencies needs destination_amount when the amount "
        "changes. The transaction is posted on a manual account and pending on a bank-connected one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": _ID, "nominal_date": _DATE,
            "amount": {"type": "number", "exclusiveMinimum": 0},
            "date": _DATE,
            "destination_amount": {"type": "number", "exclusiveMinimum": 0},
            "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id", "nominal_date"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_record_occurrence(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, nominal_date: str,
    amount: float | None = None, date: str | None = None, destination_amount: float | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    nominal = _date(nominal_date)
    if rec is None or nominal is None:
        return {"error": lifecycle.NOT_FOUND if rec is None else "nominal_date is not a date"}
    current = await _state(session, rec, nominal)
    if current is None or current.nominal_date != nominal:
        return {"error": lifecycle.NOT_AN_OCCURRENCE}
    if current.state not in ("scheduled", "overdue"):
        return {"error": lifecycle.ALREADY_HANDLED}
    try:
        new_amount, received = _decimal(amount), _decimal(destination_amount)
    except ValueError as exc:
        return {"error": str(exc)}
    on = _date(date) if date else None
    if date and on is None:
        return {"error": "date is not a date"}
    body = {
        key: value for key, value in (
            ("amount", str(new_amount) if new_amount is not None else None),
            ("date", on.isoformat() if on else None),
            ("destination_amount", str(received) if received is not None else None),
        ) if value is not None
    }
    preview = {
        "kind": "record_occurrence",
        "target": {**_target(rec), "occurrence": _occurrence(current)},
        "proposed": {
            "date": (on or current.date).isoformat(),
            "amount": num(new_amount if new_amount is not None else current.amount),
            "destination_amount": num(received),
        },
        "apply_endpoint": f"POST /api/recurring-transactions/{rec.id}/occurrences/{nominal}/record",
        "apply_body": body,
    }
    if _can_apply(ctx, apply):
        try:
            row = await lifecycle.record(session, ws_id, rec.id, nominal, amount=new_amount, on=on, received=received)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True, "transaction_id": str(row.id), "status": row.status}
    return preview


@tool(
    name="propose_link_occurrence",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for making an existing transaction one occurrence of a recurring "
        "transaction (e.g. an overdue bill already paid by a transaction typed by hand). Same "
        "account and direction, not linked to another recurring transaction; not for transfers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": _ID, "nominal_date": _DATE, "transaction_id": _ID, "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id", "nominal_date", "transaction_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_link_occurrence(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, nominal_date: str,
    transaction_id: str, apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    nominal, tx_id = _date(nominal_date), _uuid(transaction_id)
    if rec is None or nominal is None or tx_id is None:
        return {"error": lifecycle.NOT_FOUND if rec is None else "nominal_date or transaction_id is invalid"}
    current = await _state(session, rec, nominal)
    if current is None or current.nominal_date != nominal:
        return {"error": lifecycle.NOT_AN_OCCURRENCE}
    if current.state not in ("scheduled", "overdue"):
        return {"error": lifecycle.ALREADY_HANDLED}
    candidates = {tx.id: tx for tx in await lifecycle.link_candidates(session, ws_id, rec.id, nominal, days=366)}
    tx = candidates.get(tx_id)
    if tx is None:
        return {"error": lifecycle.TRANSFER_LINK if rec.destination_account_id else lifecycle.LINK_MISMATCH}
    preview = {
        "kind": "link_occurrence",
        "target": {**_target(rec), "occurrence": _occurrence(current)},
        "proposed": {
            "transaction_id": str(tx.id), "date": tx.date.isoformat(), "description": tx.description,
            "amount": num(tx.amount),
        },
        "apply_endpoint": f"POST /api/recurring-transactions/{rec.id}/occurrences/{nominal}/link",
        "apply_body": {"transaction_id": str(tx.id)},
    }
    if _can_apply(ctx, apply):
        try:
            await lifecycle.link(session, ws_id, rec.id, nominal, tx.id)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True}
    return preview


_CHANGE_FIELDS = {
    "description": {"type": "string"},
    "amount": {"type": "number", "exclusiveMinimum": 0},
    "type": {"type": "string", "enum": ["debit", "credit"]},
    "account_id": _ID,
    "to_account_id": _ID,
    "destination_amount": {"type": "number", "exclusiveMinimum": 0},
    "category_id": _ID,
    "frequency": {"type": "string", "enum": ["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]},
    "day_of_month": {"type": "integer", "minimum": 1, "maximum": 31},
    "weekend_adjustment": {"type": "string", "enum": ["none", "previous_friday", "next_monday"]},
}


def _update(changes: dict[str, Any]) -> RecurringTransactionUpdate:
    values = dict(changes)
    if "to_account_id" in values:
        values["destination_account_id"] = values.pop("to_account_id")
    for key in ("amount", "destination_amount"):
        if values.get(key) is not None:
            values[key] = str(values[key])
    return RecurringTransactionUpdate.model_validate(values)


@tool(
    name="propose_change_recurring_from",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for changing a recurring transaction from a date on (e.g. 'salary is "
        "3300 from March', 'the subscription costs 45 from next month'): the series ends the day "
        "before and continues with the new values, so earlier occurrences and posted history keep "
        "the old ones. Without from_date the whole series is corrected instead (for a mistake). "
        "The preview lists what happens to transactions already recorded: scheduled ones follow "
        "the change (removed when the dates change), ones edited on their own keep their values, "
        "posted history never changes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": _ID,
            "from_date": _DATE,
            "changes": {"type": "object", "properties": _CHANGE_FIELDS, "additionalProperties": False},
            "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id", "changes"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_change_recurring_from(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, changes: dict[str, Any],
    from_date: str | None = None, apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    if rec is None:
        return {"error": lifecycle.NOT_FOUND}
    since = _date(from_date) if from_date else None
    if from_date and since is None:
        return {"error": "from_date is not a date"}
    try:
        update = _update(changes)
    except ValidationError as exc:
        return {"error": exc.errors()[0]["msg"]}
    target = _target(rec)
    body = {"from_date": since.isoformat() if since else None, "changes": update.model_dump(mode="json", exclude_unset=True)}
    try:
        if since is None:
            impact = await series.whole_series_impact(session, ws_id, rec.id, update)
        else:
            impact = (await series.change_from(session, ws_id, rec.id, since, update, preview=True)).impact
    except _ERRORS as exc:
        return {"error": str(exc)}
    preview = {
        "kind": "change_recurring_from" if since else "correct_recurring_series",
        "target": target,
        "changes": body["changes"],
        "from_date": body["from_date"],
        "impact": impact_read(impact).model_dump(mode="json"),
        "apply_endpoint": f"POST /api/recurring-transactions/{target['id']}/change-from",
        "apply_body": body,
    }
    if _can_apply(ctx, apply):
        try:
            if since is None:
                updated, _ = await series.correct_whole_series(session, ws_id, rec.id, update)
                return {**preview, "applied": True, "id": str(updated.id)}
            result = await series.change_from(session, ws_id, rec.id, since, update, preview=False)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        assert result.schedule is not None
        return {**preview, "applied": True, "id": str(result.schedule.id)}
    return preview


@tool(
    name="propose_end_recurring",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for ending a recurring transaction on a last date (cancelling every "
        "occurrence after it; e.g. 'I cancelled the gym, the last payment is November'). It moves "
        "to Inactive. delete_scheduled also deletes the transactions already scheduled after that "
        "date (the preview lists them). To stop for a while, use "
        "propose_update_recurring_transaction with is_active=false instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": _ID, "last_date": _DATE,
            "delete_scheduled": {"type": "boolean", "default": False}, "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id", "last_date"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_end_recurring(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, last_date: str,
    delete_scheduled: bool = False, apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    last = _date(last_date)
    if rec is None or last is None:
        return {"error": lifecycle.NOT_FOUND if rec is None else "last_date is not a date"}
    target = _target(rec)
    try:
        _, impact = await series.end_series(session, ws_id, rec.id, last, delete_scheduled=delete_scheduled, preview=True)
    except _ERRORS as exc:
        return {"error": str(exc)}
    preview = {
        "kind": "end_recurring",
        "target": target,
        "proposed": {"last_date": last.isoformat(), "delete_scheduled": bool(delete_scheduled)},
        "impact": impact_read(impact).model_dump(mode="json"),
        "apply_endpoint": f"POST /api/recurring-transactions/{target['id']}/end",
        "apply_body": {"last_date": last.isoformat(), "delete_scheduled": bool(delete_scheduled)},
    }
    if _can_apply(ctx, apply):
        try:
            await series.end_series(session, ws_id, rec.id, last, delete_scheduled=delete_scheduled, preview=False)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True}
    return preview


@tool(
    name="propose_delete_recurring",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for deleting a recurring transaction (its whole series). mode "
        "'recoverable' (default) moves it to Deleted: nothing is recorded, forecast or matched any "
        "more, its recorded transactions stay, and propose_restore_recurring brings it back. "
        "mode 'permanent' works only on a deleted one: it is erased with its skips and edits; its "
        "transactions stay without the recurring link. delete_scheduled (recoverable only) also "
        "deletes transactions scheduled after today."
    ),
    parameters={
        "type": "object",
        "properties": {
            "recurring_id": _ID,
            "mode": {"type": "string", "enum": ["recoverable", "permanent"], "default": "recoverable"},
            "delete_scheduled": {"type": "boolean", "default": False},
            "apply": _APPLY_FIELD,
        },
        "required": ["recurring_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_delete_recurring(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, mode: str = "recoverable",
    delete_scheduled: bool = False, apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    if rec is None:
        return {"error": lifecycle.NOT_FOUND}
    target = _target(rec)
    try:
        if mode == "permanent":
            _, kept = await series.purge(session, ws_id, rec.id, preview=True)
            details: dict[str, Any] = {"kept_transactions": kept}
            endpoint, body = f"POST /api/recurring-transactions/{target['id']}/purge", {}
        else:
            _, impact = await series.trash(session, ws_id, rec.id, delete_scheduled=delete_scheduled, preview=True)
            details = {"impact": impact_read(impact).model_dump(mode="json")}
            endpoint = f"POST /api/recurring-transactions/{target['id']}/trash"
            body = {"delete_scheduled": bool(delete_scheduled)}
    except _ERRORS as exc:
        return {"error": str(exc)}
    preview = {
        "kind": "delete_recurring", "mode": mode, "target": target, **details,
        "apply_endpoint": endpoint, "apply_body": body,
    }
    if _can_apply(ctx, apply):
        try:
            if mode == "permanent":
                await series.purge(session, ws_id, rec.id, preview=False)
            else:
                await series.trash(session, ws_id, rec.id, delete_scheduled=delete_scheduled, preview=False)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True}
    return preview


@tool(
    name="propose_restore_recurring",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for restoring a deleted recurring transaction (its whole series) as it "
        "was. Occurrences that fell while it was deleted are recorded as skipped (listed in the "
        "preview), so nothing is backfilled."
    ),
    parameters={
        "type": "object",
        "properties": {"recurring_id": _ID, "apply": _APPLY_FIELD},
        "required": ["recurring_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "recurring"],
)
async def propose_restore_recurring(
    *, session: AsyncSession, ctx: CallContext, recurring_id: str, apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rec = await _schedule(session, ws_id, recurring_id)
    if rec is None:
        return {"error": lifecycle.NOT_FOUND}
    target = _target(rec)
    try:
        plan = await series.restore(session, ws_id, rec.id, preview=True)
    except _ERRORS as exc:
        return {"error": str(exc)}
    preview = {
        "kind": "restore_recurring",
        "target": target,
        "skipped": [day.isoformat() for day in plan.skipped],
        "next_occurrence": plan.next_occurrence.isoformat() if plan.next_occurrence else None,
        "apply_endpoint": f"POST /api/recurring-transactions/{target['id']}/restore",
        "apply_body": {},
    }
    if _can_apply(ctx, apply):
        try:
            await series.restore(session, ws_id, rec.id, preview=False)
        except _ERRORS as exc:
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True}
    return preview
