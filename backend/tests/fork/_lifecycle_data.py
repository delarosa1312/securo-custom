"""Synthetic schedules for the F-005 lifecycle tests (dates relative to today)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select

from app.core.app_clock import app_today
from app.fork.models import RecurringOccurrence
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction


def month_day(offset: int, day: int) -> date:
    """Day ``day`` of the month ``offset`` months from today's (negative: earlier)."""
    today = app_today()
    index = today.month - 1 + offset
    return date(today.year + index // 12, index % 12 + 1, day)


async def schedule(
    session, user, ws, acc, *, amount="1500.00", start: Optional[date] = None, day: Optional[int] = None,
    frequency="monthly", type_="debit", description="Rent", weekend="none", end: Optional[date] = None,
    pointer: Optional[date] = None, auto=True, active=True, category_id=None,
) -> RecurringTransaction:
    start = start or month_day(1, 10)
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=acc.id,
        description=description, amount=Decimal(amount), currency=acc.currency, type=type_,
        frequency=frequency, weekend_adjustment=weekend, day_of_month=day or start.day,
        start_date=start, end_date=end, is_active=active, auto_generate=auto,
        next_occurrence=pointer or start, category_id=category_id,
    )
    session.add(rec)
    await session.flush()
    return rec


async def linked_rows(session, rec_ids) -> list[Transaction]:
    if not isinstance(rec_ids, (list, tuple, set)):
        rec_ids = [rec_ids]
    return list(
        (
            await session.execute(
                select(Transaction)
                .where(Transaction.recurring_transaction_id.in_(list(rec_ids)))
                .order_by(Transaction.date, Transaction.type.desc())
            )
        ).scalars().all()
    )


async def ledger(session, rec_id) -> dict[date, str]:
    rows = (
        await session.execute(
            select(RecurringOccurrence).where(RecurringOccurrence.recurring_transaction_id == rec_id)
        )
    ).scalars().all()
    return {row.occurrence_date: row.state for row in rows}


def far() -> date:
    return app_today() + timedelta(days=500)


async def other_workspace(session, user):
    from app.models.workspace import Workspace

    other = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=user.id)
    session.add(other)
    await session.flush()
    return other
