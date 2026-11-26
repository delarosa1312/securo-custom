"""Synthetic accounts and transfer schedules for the F-004 tests."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction


async def account(
    session, user, ws, name, *, type_="checking", currency="BRL", opening="1000.00",
    include=True, closed=False, connection_id=None,
) -> Account:
    acc = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, name=name, type=type_,
        balance=Decimal(opening), currency=currency, include_in_available=include,
        is_closed=closed, connection_id=connection_id,
    )
    session.add(acc)
    await session.flush()
    if Decimal(opening):
        opened = app_today() - timedelta(days=60)
        session.add(
            Transaction(
                id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=acc.id,
                description="Opening balance", amount=Decimal(opening), amount_primary=Decimal(opening),
                currency=currency, date=opened, effective_date=opened,
                type="debit" if type_ in ("credit_card", "loan") else "credit",
                source="opening_balance", status="posted",
            )
        )
    return acc


async def transfer_schedule(
    session, user, ws, source, destination, *, amount="200.00", start: Optional[date] = None,
    frequency="monthly", day: Optional[int] = None, weekend="none", end: Optional[date] = None,
    destination_amount: Optional[str] = None, description="Savings plan", active=True,
) -> RecurringTransaction:
    start = start or next_month_day(10)
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=source.id,
        destination_account_id=destination.id,
        destination_amount=Decimal(destination_amount) if destination_amount else None,
        description=description, amount=Decimal(amount), currency=source.currency, type="debit",
        frequency=frequency, weekend_adjustment=weekend, day_of_month=day or start.day,
        start_date=start, end_date=end, is_active=active, auto_generate=True,
        next_occurrence=start,
    )
    session.add(rec)
    await session.flush()
    return rec


def next_month_day(day: int) -> date:
    today = app_today()
    first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first.replace(day=day)
