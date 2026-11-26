"""Request and response shapes of the recurring lifecycle routes (F-005)."""

from __future__ import annotations

import uuid
import datetime as dt
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.recurring_transaction import RecurringTransactionRead, RecurringTransactionUpdate


class OccurrenceRead(BaseModel):
    recurring_id: uuid.UUID
    nominal_date: dt.date
    date: dt.date
    amount: Decimal
    destination_amount: Optional[Decimal] = None
    # recorded | edited | skipped | removed | scheduled | overdue | past
    state: str
    transaction_id: Optional[uuid.UUID] = None


class OverdueRead(BaseModel):
    recurring_id: uuid.UUID
    description: str
    account_id: Optional[uuid.UUID] = None
    destination_account_id: Optional[uuid.UUID] = None
    type: str
    amount: Decimal
    currency: str
    nominal_date: dt.date
    date: dt.date
    auto_generate: bool


class RecordOccurrence(BaseModel):
    """Record one occurrence now; any value given replaces the schedule's for it alone."""

    model_config = ConfigDict(extra="forbid")

    amount: Optional[Decimal] = Field(default=None, gt=0)
    date: Optional[dt.date] = None
    destination_amount: Optional[Decimal] = Field(default=None, gt=0)


class LinkOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: uuid.UUID


class RecordedOccurrence(BaseModel):
    recurring_id: uuid.UUID
    nominal_date: dt.date
    transaction_id: uuid.UUID
    date: dt.date
    amount: Decimal
    status: str
    state: str


class LinkCandidate(BaseModel):
    id: uuid.UUID
    date: dt.date
    description: str
    amount: Decimal
    currency: str
    status: str


class RowImpactRead(BaseModel):
    transaction_id: uuid.UUID
    date: dt.date
    amount: Decimal
    status: str
    transfer_pair_id: Optional[uuid.UUID] = None


class ImpactRead(BaseModel):
    """What an action does to the transactions the series already wrote."""

    history: int
    follow: list[RowImpactRead]
    removed: list[RowImpactRead]
    edited: list[RowImpactRead]
    skipped_kept: list[dt.date]
    skipped_dropped: list[dt.date]


class ChangeFrom(BaseModel):
    """``from_date`` set: the series changes from that date on (a split). Null: the whole series
    is corrected (upstream's edit) and the transactions still to come follow it."""

    model_config = ConfigDict(extra="forbid")

    from_date: Optional[dt.date] = None
    changes: RecurringTransactionUpdate
    preview: bool = False


class ChangeResult(BaseModel):
    schedule: Optional[RecurringTransactionRead] = None
    impact: ImpactRead
    preview: bool


class EndSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_date: dt.date
    delete_scheduled: bool = False
    preview: bool = False


class EndResult(BaseModel):
    schedule: Optional[RecurringTransactionRead] = None
    impact: ImpactRead
    preview: bool


class TrashSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delete_scheduled: bool = False
    preview: bool = False


class TrashResult(BaseModel):
    deleted: list[uuid.UUID]
    impact: ImpactRead
    preview: bool


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: bool = False


class RestoreResult(BaseModel):
    restored: list[uuid.UUID]
    skipped: list[dt.date]
    next_occurrence: Optional[dt.date] = None
    preview: bool


class PurgeResult(BaseModel):
    deleted: list[uuid.UUID]
    kept_transactions: int
    preview: bool
