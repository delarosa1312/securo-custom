"""F-006 reconciliation dataset: one base plus one component at a time. Synthetic data only.

Today is pinned to Friday 2026-11-20. The base is a checking account opened
with 1,000.00 and a savings account opened with 500.00 on 2026-10-01, so every
view starts from 1,500.00 and a component's effect is the difference from it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.invoice import Invoice

from tests.fork import _budget_data as data

OPENED = date(2026, 9, 1)
BASE_TOTAL = Decimal("1500.00")


class Base:
    def __init__(self, session, user, ws):
        self.session, self.user, self.ws = session, user, ws

    async def build(self) -> "Base":
        s, u, w = self.session, self.user, self.ws
        self.checking = await data.account(s, u, w, "Checking")
        self.savings = await data.account(s, u, w, "Savings", acct_type="savings")
        await data.txn(s, u, w, self.checking, "1000", OPENED, "credit", source="opening_balance")
        await data.txn(s, u, w, self.savings, "500", OPENED, "credit", source="opening_balance")
        return self

    async def txn(self, acct, amount, when, txn_type="debit", **extra):
        return await data.txn(self.session, self.user, self.ws, acct, amount, when, txn_type, **extra)

    async def category(self, name, **flags):
        return await data.category(self.session, self.user, self.ws, name, **flags)

    async def rule(self, acct, amount, next_occurrence, txn_type="debit", cat=None, **extra):
        rule = await data.recurring(self.session, self.user, self.ws, acct, amount, next_occurrence, cat, txn_type)
        for key, value in extra.items():
            setattr(rule, key, value)
        await self.session.commit()
        return rule

    async def transfer(self, src, dst, amount, when, status="posted"):
        pair = uuid.uuid4()
        out_leg = await self.txn(src, amount, when, "debit", status=status, source="transfer")
        in_leg = await self.txn(dst, amount, when, "credit", status=status, source="transfer")
        out_leg.transfer_pair_id = in_leg.transfer_pair_id = pair
        await self.session.commit()
        return out_leg, in_leg

    async def invoice(self, amount, due, direction="receivable"):
        row = Invoice(
            id=uuid.uuid4(), workspace_id=self.ws.id, user_id=self.user.id, status="open", origin="imported",
            direction=direction, issue_date=date(2026, 11, 1), due_date=due, currency="BRL",
            total=Decimal(amount), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        return await data.add(self.session, row)


async def _pending_future(b: Base):
    await b.txn(b.checking, "100", date(2026, 11, 25), status="pending")


async def _pending_overdue(b: Base):
    await b.txn(b.checking, "50", date(2026, 11, 10), status="pending")


async def _future_posted_installment(b: Base):
    await b.txn(b.checking, "80", date(2026, 12, 5))


async def _recurring(b: Base):
    await b.rule(b.checking, "30", date(2026, 11, 28))


async def _recurring_materialized(b: Base):
    rule = await b.rule(b.checking, "30", date(2026, 11, 28))
    row = await b.txn(b.checking, "30", date(2026, 11, 28), source="recurring")
    row.recurring_transaction_id = rule.id
    await b.session.commit()


async def _recurring_overdue(b: Base):
    await b.rule(b.checking, "30", date(2026, 11, 15), auto_generate=False)


async def _recurring_due_today(b: Base):
    await b.rule(b.checking, "30", date(2026, 11, 20), auto_generate=False)


async def _transfer_future(b: Base):
    await b.transfer(b.checking, b.savings, "200", date(2026, 12, 3), status="pending")


async def _transfer_past(b: Base):
    await b.transfer(b.checking, b.savings, "300", date(2026, 11, 5))


async def _recurring_transfer_like(b: Base):
    investments = await b.category("Investments", treat_as_transfer=True)
    await b.rule(b.checking, "500", date(2026, 11, 25), cat=investments)


async def _ignored_rule(b: Base):
    ignored = await b.category("Ignored", is_ignored=True)
    await b.rule(b.checking, "70", date(2026, 11, 26), cat=ignored)


async def _closed_account_rule(b: Base):
    closed = await data.account(b.session, b.user, b.ws, "Old", is_closed=True)
    await b.rule(closed, "40", date(2026, 11, 26))


async def _investment_account_rule(b: Base):
    broker = await data.account(b.session, b.user, b.ws, "Broker", acct_type="investment")
    await b.rule(broker, "40", date(2026, 11, 26), txn_type="credit")


async def _excluded_from_pnl_past(b: Base):
    row = await b.txn(b.checking, "60", date(2026, 11, 5))
    row.exclude_from_pnl = True
    await b.session.commit()


async def _card_purchase_and_installment(b: Base):
    card = await data.account(
        b.session, b.user, b.ws, "Card", acct_type="credit_card", statement_close_day=1, payment_due_day=10,
    )
    await b.txn(card, "120", date(2026, 11, 18), effective_date=date(2026, 12, 10))
    await b.txn(card, "120", date(2026, 12, 18), effective_date=date(2027, 1, 10))


async def _invoice_claim(b: Base):
    await b.invoice("400", date(2026, 11, 27))


async def _foreign_pending(b: Base):
    usd = await data.account(b.session, b.user, b.ws, "USD", currency="USD")
    await data.usd_rate(b.session, "BRL", "5")
    await b.txn(usd, "100", OPENED, "credit", source="opening_balance", amount_primary="500")
    await b.txn(usd, "10", date(2026, 11, 25), status="pending", amount_primary="50")


async def _connected_pending(b: Base):
    from app.models.bank_connection import BankConnection

    conn = await data.add(b.session, BankConnection(
        id=uuid.uuid4(), user_id=b.user.id, workspace_id=b.ws.id, provider="test", external_id="synthetic",
        institution_name="Synthetic Bank", credentials={}, status="active",
        created_at=datetime.now(timezone.utc),
    ))
    bank = await data.account(b.session, b.user, b.ws, "Bank", connection_id=conn.id)
    bank.balance = Decimal("300")  # the provider's figure, which holds its own pending row below
    await b.session.commit()
    await b.txn(bank, "15", date(2026, 11, 18), status="pending")
    await b.txn(bank, "25", date(2026, 11, 18), status="pending", source="recurring")


async def _pending_overdue_and_today(b: Base):
    await b.txn(b.checking, "50", date(2026, 11, 10), status="pending")
    await b.txn(b.checking, "20", date(2026, 11, 20), status="pending")


COMPONENTS = {
    "base": None,
    "pending_future": _pending_future,
    "pending_overdue": _pending_overdue,
    "future_posted_installment": _future_posted_installment,
    "recurring": _recurring,
    "recurring_materialized": _recurring_materialized,
    "recurring_overdue": _recurring_overdue,
    "recurring_due_today": _recurring_due_today,
    "transfer_future": _transfer_future,
    "transfer_past": _transfer_past,
    "recurring_transfer_like": _recurring_transfer_like,
    "ignored_rule": _ignored_rule,
    "closed_account_rule": _closed_account_rule,
    "investment_account_rule": _investment_account_rule,
    "excluded_from_pnl_past": _excluded_from_pnl_past,
    "card_purchase_and_installment": _card_purchase_and_installment,
    "invoice_claim": _invoice_claim,
    "foreign_pending": _foreign_pending,
    "connected_pending": _connected_pending,
    "pending_overdue_and_today": _pending_overdue_and_today,
}


async def build(session, user, ws, component: str) -> Base:
    base = await Base(session, user, ws).build()
    builder = COMPONENTS[component]
    if builder is not None:
        await builder(base)
    return base

