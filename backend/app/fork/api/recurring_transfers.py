"""Conversion of existing individually scheduled transfers into recurring transfers (F-004).

`GET /api/recurring-transactions/transfer-conversions` previews candidate series and changes
nothing; `POST /api/recurring-transactions/transfer-conversions/{series_id}/apply` converts one
series. Mounted before upstream's recurring router; its `/{recurring_id}` paths never take these.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.fork import transfer_conversion
from app.schemas.recurring_transaction import RecurringTransactionRead

router = APIRouter(prefix="/api/recurring-transactions", tags=["recurring-transactions"])


class ConversionAccount(BaseModel):
    id: uuid.UUID
    name: str
    currency: str


class ConversionTransfer(BaseModel):
    transfer_pair_id: uuid.UUID
    date: date
    debit_transaction_id: uuid.UUID
    credit_transaction_id: uuid.UUID
    status: str


class ConversionSeries(BaseModel):
    series_id: uuid.UUID
    description: str
    source_account: ConversionAccount
    destination_account: ConversionAccount
    amount: Decimal
    destination_amount: Optional[Decimal] = None
    frequency: str
    weekend_adjustment: str
    day_of_month: int
    start_date: date
    first_date: date
    last_date: date
    count: int
    transfers: list[ConversionTransfer]


class ConversionPreview(BaseModel):
    series: list[ConversionSeries]
    left_alone: int


class ConversionApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Default: the schedule ends at the series' last transfer, so no number moves.
    end_date: Optional[date] = None
    open_ended: bool = False


class ConversionResult(BaseModel):
    schedule: RecurringTransactionRead
    linked_transfers: int


def _account(account) -> ConversionAccount:
    return ConversionAccount(
        id=account.id, name=account.display_name or account.name, currency=account.currency
    )


def serialize(series: transfer_conversion.Series) -> ConversionSeries:
    return ConversionSeries(
        series_id=series.series_id,
        description=series.description,
        source_account=_account(series.source),
        destination_account=_account(series.destination),
        amount=series.amount,
        destination_amount=series.destination_amount,
        frequency=series.frequency,
        weekend_adjustment=series.weekend_adjustment,
        day_of_month=series.day_of_month,
        start_date=series.start_date,
        first_date=series.pairs[0].debit.date,
        last_date=series.pairs[-1].debit.date,
        count=len(series.pairs),
        transfers=[
            ConversionTransfer(
                transfer_pair_id=pair.pair_id,
                date=pair.debit.date,
                debit_transaction_id=pair.debit.id,
                credit_transaction_id=pair.credit.id,
                status=pair.debit.status,
            )
            for pair in series.pairs
        ],
    )


@router.get("/transfer-conversions", response_model=ConversionPreview)
async def preview_transfer_conversions(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Future transfers that could become one recurring transfer each series. Read only."""
    result = await transfer_conversion.preview(session, ctx.workspace.id)
    return ConversionPreview(series=[serialize(s) for s in result.series], left_alone=result.left_alone)


@router.post(
    "/transfer-conversions/{series_id}/apply",
    response_model=ConversionResult,
    status_code=status.HTTP_201_CREATED,
)
async def apply_transfer_conversion(
    series_id: uuid.UUID,
    data: Optional[ConversionApply] = None,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Convert one previewed series: create its recurring transfer and link its transfers."""
    data = data or ConversionApply()
    try:
        schedule, linked = await transfer_conversion.apply(
            session, ctx.workspace.id, ctx.user_id, series_id,
            end_date=data.end_date, open_ended=data.open_ended,
        )
    except transfer_conversion.ConversionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ConversionResult(
        schedule=RecurringTransactionRead.model_validate(schedule), linked_transfers=linked
    )
