"""Recurring lifecycle routes (F-005, issue #7), under `/api/recurring-transactions`.

Occurrences one at a time (list, skip, undo skip, record ahead, link a transaction), the overdue
list (Q12), and the whole series (change from a date, correct, end, delete, restore, delete
permanently). Every action that changes rows has `preview`, which changes nothing. Upstream's
`DELETE /{id}` stays the permanent delete. Mounted before upstream's recurring router.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.fork import recurring_lifecycle as lifecycle
from app.fork import recurring_series as series
from app.fork.models import RecurringOccurrence
from app.fork.recurring_lifecycle_schemas import (
    ChangeFrom,
    ChangeResult,
    EndResult,
    EndSeries,
    ImpactRead,
    LinkCandidate,
    LinkOccurrence,
    OccurrenceRead,
    OverdueRead,
    Preview,
    PurgeResult,
    RecordedOccurrence,
    RecordOccurrence,
    RestoreResult,
    RowImpactRead,
    TrashResult,
    TrashSeries,
)
from app.models.recurring_transaction import RecurringTransaction
from app.schemas.recurring_transaction import RecurringTransactionRead
from app.services.recurring_transaction_service import RecurringConflictError

router = APIRouter(prefix="/api/recurring-transactions", tags=["recurring-transactions"])


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, lifecycle.LifecycleNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (lifecycle.LifecycleConflict, RecurringConflictError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


_ERRORS = (lifecycle.LifecycleNotFound, lifecycle.LifecycleConflict, RecurringConflictError, ValueError)


def impact_read(impact: series.Impact) -> ImpactRead:
    def rows(items):
        return [RowImpactRead(**vars(item)) for item in items]

    return ImpactRead(
        history=impact.history,
        follow=rows(impact.follow),
        removed=rows(impact.removed),
        edited=rows(impact.edited),
        skipped_kept=impact.skipped_kept,
        skipped_dropped=impact.skipped_dropped,
    )


async def _schedule(session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID) -> RecurringTransaction:
    rec = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.id == recurring_id,
                RecurringTransaction.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=lifecycle.NOT_FOUND)
    return rec


@router.get("/overdue", response_model=list[OverdueRead])
async def list_overdue(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Occurrences dated today or earlier that are not transactions yet (they count nowhere)."""
    found = await lifecycle.overdue(session, ctx.workspace.id, app_today())
    return [
        OverdueRead(
            recurring_id=item.schedule.id,
            description=item.schedule.description,
            account_id=item.schedule.account_id,
            destination_account_id=item.schedule.destination_account_id,
            type=item.schedule.type,
            amount=item.schedule.amount,
            currency=item.schedule.currency,
            nominal_date=item.nominal_date,
            date=item.date,
            auto_generate=bool(item.schedule.auto_generate),
        )
        for item in found
    ]


@router.get("/{recurring_id}/occurrences", response_model=list[OccurrenceRead])
async def list_occurrences(
    recurring_id: uuid.UUID,
    from_date: Optional[date] = Query(None, alias="from", description="Default: three months ago"),
    to_date: Optional[date] = Query(None, alias="to", description="Inclusive; default: a year ahead"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The schedule's occurrences with their state (at most 60)."""
    today = app_today()
    start = from_date or today - timedelta(days=92)
    end = (to_date or today + timedelta(days=366)) + timedelta(days=1)
    if start >= end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="from must not be after to")
    rec = await _schedule(session, ctx.workspace.id, recurring_id)
    listed = await lifecycle.list_occurrences(session, rec, start, end, today)
    return [OccurrenceRead(**vars(item)) for item in listed]


@router.post("/{recurring_id}/occurrences/{nominal_date}/skip", status_code=status.HTTP_204_NO_CONTENT)
async def skip_occurrence(
    recurring_id: uuid.UUID,
    nominal_date: date,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        await lifecycle.skip(session, ctx.workspace.id, recurring_id, nominal_date)
    except _ERRORS as exc:
        raise _http(exc)


@router.delete("/{recurring_id}/occurrences/{nominal_date}/skip", status_code=status.HTTP_204_NO_CONTENT)
async def unskip_occurrence(
    recurring_id: uuid.UUID,
    nominal_date: date,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        await lifecycle.unskip(session, ctx.workspace.id, recurring_id, nominal_date)
    except _ERRORS as exc:
        raise _http(exc)


async def _recorded(session: AsyncSession, recurring_id: uuid.UUID, nominal: date, tx) -> RecordedOccurrence:
    state = (
        await session.execute(
            select(RecurringOccurrence.state).where(
                RecurringOccurrence.recurring_transaction_id == recurring_id,
                RecurringOccurrence.occurrence_date == nominal,
            )
        )
    ).scalar_one()
    return RecordedOccurrence(
        recurring_id=recurring_id, nominal_date=nominal, transaction_id=tx.id, date=tx.date,
        amount=tx.amount, status=tx.status, state=state,
    )


@router.post(
    "/{recurring_id}/occurrences/{nominal_date}/record",
    response_model=RecordedOccurrence,
    status_code=status.HTTP_201_CREATED,
)
async def record_occurrence(
    recurring_id: uuid.UUID,
    nominal_date: date,
    data: Optional[RecordOccurrence] = None,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Record one occurrence now as a transaction, as scheduled or with its own amount or date."""
    data = data or RecordOccurrence()
    try:
        tx = await lifecycle.record(
            session, ctx.workspace.id, recurring_id, nominal_date,
            amount=data.amount, on=data.date, received=data.destination_amount,
        )
    except _ERRORS as exc:
        raise _http(exc)
    return await _recorded(session, recurring_id, nominal_date, tx)


@router.get("/{recurring_id}/occurrences/{nominal_date}/link-candidates", response_model=list[LinkCandidate])
async def link_candidates(
    recurring_id: uuid.UUID,
    nominal_date: date,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Unlinked transactions on the schedule's account and direction near the occurrence's date."""
    try:
        rows = await lifecycle.link_candidates(session, ctx.workspace.id, recurring_id, nominal_date)
    except _ERRORS as exc:
        raise _http(exc)
    return [
        LinkCandidate(
            id=tx.id, date=tx.date, description=tx.description, amount=tx.amount,
            currency=tx.currency, status=tx.status,
        )
        for tx in rows[:20]
    ]


@router.post(
    "/{recurring_id}/occurrences/{nominal_date}/link",
    response_model=RecordedOccurrence,
)
async def link_occurrence(
    recurring_id: uuid.UUID,
    nominal_date: date,
    data: LinkOccurrence,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Make an existing transaction this occurrence."""
    try:
        tx = await lifecycle.link(session, ctx.workspace.id, recurring_id, nominal_date, data.transaction_id)
    except _ERRORS as exc:
        raise _http(exc)
    return await _recorded(session, recurring_id, nominal_date, tx)


@router.post("/{recurring_id}/change-from", response_model=ChangeResult)
async def change_from(
    recurring_id: uuid.UUID,
    data: ChangeFrom,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Change the series from a date on (the old values stay in history), or correct the whole
    series when ``from_date`` is null. ``preview`` lists what happens to transactions already
    recorded and changes nothing."""
    try:
        if data.from_date is None:
            if data.preview:
                impact = await series.whole_series_impact(session, ctx.workspace.id, recurring_id, data.changes)
                return ChangeResult(impact=impact_read(impact), preview=True)
            rec, impact = await series.correct_whole_series(session, ctx.workspace.id, recurring_id, data.changes)
            return ChangeResult(
                schedule=RecurringTransactionRead.model_validate(rec), impact=impact_read(impact), preview=False
            )
        result = await series.change_from(
            session, ctx.workspace.id, recurring_id, data.from_date, data.changes, preview=data.preview
        )
    except _ERRORS as exc:
        raise _http(exc)
    return ChangeResult(
        schedule=RecurringTransactionRead.model_validate(result.schedule) if result.schedule else None,
        impact=impact_read(result.impact),
        preview=result.preview,
    )


@router.post("/{recurring_id}/end", response_model=EndResult)
async def end_series(
    recurring_id: uuid.UUID,
    data: EndSeries,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """End the series on ``last_date``; optionally delete the transactions scheduled after it."""
    try:
        rec, impact = await series.end_series(
            session, ctx.workspace.id, recurring_id, data.last_date,
            delete_scheduled=data.delete_scheduled, preview=data.preview,
        )
    except _ERRORS as exc:
        raise _http(exc)
    return EndResult(
        schedule=RecurringTransactionRead.model_validate(rec) if rec else None,
        impact=impact_read(impact),
        preview=data.preview,
    )


@router.post("/{recurring_id}/trash", response_model=TrashResult)
async def trash_series(
    recurring_id: uuid.UUID,
    data: Optional[TrashSeries] = None,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Move the series to Deleted (restorable). Its recorded transactions stay."""
    data = data or TrashSeries()
    try:
        parts, impact = await series.trash(
            session, ctx.workspace.id, recurring_id,
            delete_scheduled=data.delete_scheduled, preview=data.preview,
        )
    except _ERRORS as exc:
        raise _http(exc)
    return TrashResult(deleted=parts, impact=impact_read(impact), preview=data.preview)


@router.post("/{recurring_id}/restore", response_model=RestoreResult)
async def restore_series(
    recurring_id: uuid.UUID,
    data: Optional[Preview] = None,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Bring a deleted series back; occurrences that fell while deleted are recorded as skipped."""
    data = data or Preview()
    try:
        plan = await series.restore(session, ctx.workspace.id, recurring_id, preview=data.preview)
    except _ERRORS as exc:
        raise _http(exc)
    return RestoreResult(
        restored=plan.parts, skipped=plan.skipped,
        next_occurrence=plan.next_occurrence, preview=data.preview,
    )


@router.post("/{recurring_id}/purge", response_model=PurgeResult)
async def purge_series(
    recurring_id: uuid.UUID,
    data: Optional[Preview] = None,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a deleted series permanently (upstream's delete): its transactions stay, unlinked."""
    data = data or Preview()
    try:
        parts, kept = await series.purge(session, ctx.workspace.id, recurring_id, preview=data.preview)
    except _ERRORS as exc:
        raise _http(exc)
    return PurgeResult(deleted=parts, kept_transactions=kept, preview=data.preview)
