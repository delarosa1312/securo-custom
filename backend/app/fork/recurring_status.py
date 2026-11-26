"""Which schedules a list shows (F-005): active, inactive, deleted.

Upstream's list returns every schedule. Deleted ones (in Deleted, restorable) leave it unless asked
for, so without a status the response is what upstream returns for the same schedules.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.models.recurring_transaction import RecurringTransaction

STATUSES = ("active", "inactive", "deleted", "all")
IN_DELETED = (
    "A deleted recurring transaction with the same description, frequency and start date exists; "
    "restore it or delete it permanently"
)


def with_status(stmt: Any, status: Optional[str]) -> Any:
    if status == "all":
        return stmt
    if status == "deleted":
        return stmt.where(RecurringTransaction.deleted_at.is_not(None))
    stmt = stmt.where(RecurringTransaction.deleted_at.is_(None))
    if status == "active":
        return stmt.where(RecurringTransaction.is_active.is_(True))
    if status == "inactive":
        return stmt.where(RecurringTransaction.is_active.is_(False))
    return stmt


async def duplicate_message(session, workspace_id: uuid.UUID, user_id: uuid.UUID, data: Any, default: str) -> str:
    """Upstream's duplicate message, or where the duplicate is when it sits in Deleted."""
    deleted = (
        await session.execute(
            select(RecurringTransaction.id).where(
                RecurringTransaction.workspace_id == workspace_id,
                RecurringTransaction.user_id == user_id,
                RecurringTransaction.description == data.description,
                RecurringTransaction.frequency == data.frequency,
                RecurringTransaction.start_date == data.start_date,
                RecurringTransaction.deleted_at.is_not(None),
            )
        )
    ).first()
    return IN_DELETED if deleted is not None else default
