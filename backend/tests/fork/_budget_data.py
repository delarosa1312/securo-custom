"""Synthetic rows for the budget-aware Cash Flow tests (F-002). Never real data."""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine

TODAY = date(2026, 11, 20)


def pin_today(monkeypatch, today: date = TODAY) -> date:
    """Make every `app_today()` in the app return `today`, wherever it was imported."""
    from app.core import app_clock

    original = app_clock.app_today
    for name, module in list(sys.modules.items()):
        if name.startswith(("app.", "mcp_server")) and getattr(module, "app_today", None) is original:
            monkeypatch.setattr(module, "app_today", lambda: today)
    monkeypatch.setattr(app_clock, "app_today", lambda: today)
    return today


async def add(session, *rows):
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows[0] if len(rows) == 1 else rows


async def account(session, user, workspace, name="Checking", currency="BRL", acct_type="checking", **extra):
    return await add(session, Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name,
        type=acct_type, balance=Decimal("0"), currency=currency, **extra,
    ))


async def group(session, user, workspace, name):
    return await add(session, CategoryGroup(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name))


async def category(session, user, workspace, name, group_id=None, **flags):
    return await add(session, Category(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name,
        icon="circle", color="#3B82F6", group_id=group_id, **flags,
    ))


async def budget(session, user, workspace, cat, amount, month=date(2026, 11, 1), recurring=True, currency="BRL"):
    return await add(session, Budget(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, category_id=cat.id,
        amount=Decimal(str(amount)), currency=currency, month=month, is_recurring=recurring,
    ))


async def txn(
    session, user, workspace, acct, amount, when, txn_type="debit", cat=None, status="posted",
    source="manual", currency=None, amount_primary=None, effective_date: Optional[date] = None,
):
    row = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=acct.id,
        category_id=cat.id if cat else None, description=f"Synthetic {txn_type}",
        amount=Decimal(str(amount)), currency=currency or acct.currency, date=when,
        effective_date=effective_date or when, type=txn_type, status=status, source=source,
        amount_primary=Decimal(str(amount_primary)) if amount_primary is not None else None,
        created_at=datetime.now(timezone.utc),
    )
    return await add(session, row)


async def split_txn(session, user, workspace, acct, when, parts: list[tuple[Category, str]]):
    total = sum((Decimal(amount) for _, amount in parts), Decimal("0"))
    row = await txn(session, user, workspace, acct, total, when, cat=parts[0][0])
    row.has_category_lines = True
    for position, (cat, amount) in enumerate(parts):
        session.add(TransactionCategoryLine(
            id=uuid.uuid4(), workspace_id=workspace.id, transaction_id=row.id,
            category_id=cat.id, amount=Decimal(amount), position=position,
        ))
    await session.commit()
    return row


async def recurring(session, user, workspace, acct, amount, next_occurrence, cat=None, txn_type="debit"):
    return await add(session, RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=acct.id,
        category_id=cat.id if cat else None, description="Synthetic recurring",
        amount=Decimal(str(amount)), currency=acct.currency, type=txn_type, frequency="monthly",
        day_of_month=next_occurrence.day, start_date=next_occurrence, is_active=True,
        next_occurrence=next_occurrence,
    ))


async def usd_rate(session, quote="BRL", rate="5", when=date(2026, 11, 1)):
    return await add(session, FxRate(
        id=uuid.uuid4(), base_currency="USD", quote_currency=quote, date=when,
        rate=Decimal(rate), source="test",
    ))
