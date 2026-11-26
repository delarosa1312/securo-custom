"""Predictive forecast: the scheduled layer the other projections already count (F-006, issue #8).

The predictive forecast (`cashflow_forecast_service`) covers the money that pays
bills: included checking, savings and wallet accounts, plus card spending when
it happens (card debt already owed is not due today). This module makes its
scheduled layer agree with the dashboard and the Cash Flow report on those
accounts:

- pending rows and future-dated rows (installments, generate-ahead rows) count
  on their date; one dated today or earlier counts tomorrow, as in the Cash Flow;
- on a card, spending counts when it happens after today. A card row dated today
  or earlier is debt already owed, pending or posted. Card credits (refunds) and
  both legs of a transfer touching a card (bill payments) do not count: paying
  the bill would count the spending a second time;
- a recurring occurrence that already exists as a row counts once, as that row;
- rules count only on covered accounts and outside ignored categories;
- a detected pattern does not count again where a row of the same merchant and
  direction already stands for that occurrence.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.available_balance import counts_as_available
from app.models.category import Category
from app.schemas.forecast import CadenceEnum, FlowDirectionEnum

CARD_TYPE = "credit_card"

# Half a cadence's period: a row this close to a detected occurrence is that occurrence.
_HALF_PERIOD_DAYS = {
    CadenceEnum.WEEKLY: 3,
    CadenceEnum.BI_WEEKLY: 7,
    CadenceEnum.MONTHLY: 15,
    CadenceEnum.QUARTERLY: 45,
    CadenceEnum.SEMI_ANNUAL: 91,
    CadenceEnum.ANNUAL: 182,
}


def covered_account_ids(accounts: Iterable[Mapping[str, Any]]) -> set[Any]:
    """Accounts the predictive forecast follows: included liquid ones and open cards."""
    return {
        account["id"]
        for account in accounts
        if counts_as_available(account)
        or (account.get("type") == CARD_TYPE and not account.get("is_closed", False))
    }


async def ignored_category_ids(session: AsyncSession, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    result = await session.execute(
        select(Category.id).where(Category.workspace_id == workspace_id, Category.is_ignored.is_(True))
    )
    return set(result.scalars().all())


def rule_counts(rule, covered: set[Any], ignored: set[uuid.UUID]) -> bool:
    """Whether a manual rule's occurrences belong in the forecast."""
    if rule.category_id is not None and rule.category_id in ignored:
        return False
    # A legacy rule without an account keeps upstream's treatment.
    return rule.account_id is None or rule.account_id in covered


@dataclass
class ScheduledRow:
    on: date
    name: str
    name_key: str
    direction: FlowDirectionEnum
    amount: Decimal


@dataclass
class ScheduledRows:
    rows: list[ScheduledRow] = field(default_factory=list)
    unconverted_currencies: set[str] = field(default_factory=set)

    def events(self) -> dict[date, list[tuple[str, FlowDirectionEnum, Decimal]]]:
        by_day: dict[date, list[tuple[str, FlowDirectionEnum, Decimal]]] = {}
        for row in self.rows:
            by_day.setdefault(row.on, []).append((row.name, row.direction, row.amount))
        return by_day

    def covers(self, name_key: str, direction: FlowDirectionEnum, day: date, cadence: CadenceEnum, names_match) -> bool:
        """Whether a row already stands for a detected occurrence on ``day``."""
        window = _HALF_PERIOD_DAYS.get(cadence)
        if window is None:
            return False
        return any(
            row.direction == direction
            and abs((row.on - day).days) <= window
            and names_match(name_key, row.name_key)
            for row in self.rows
        )


async def scheduled_rows(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    covered: set[Any],
    today: date,
    end_date: date,
    base_currency: str,
    normalize,
    convert_or_none,
) -> ScheduledRows:
    """Pending and future-dated rows on covered accounts through ``end_date``, in the base currency."""
    from app.services.dashboard_service import _get_forecast_transactions, _signed_transaction_amount

    found = ScheduledRows()
    if not covered:
        return found
    tomorrow = today + timedelta(days=1)
    rows = await _get_forecast_transactions(
        session, workspace_id, date.min, end_date + timedelta(days=1), list(covered),
    )
    card_pairs = {tx.transfer_pair_id for tx in rows if tx.transfer_pair_id and _on_card(tx)}
    for tx in rows:
        if tx.is_ignored or (tx.category and tx.category.is_ignored):
            continue
        if tx.transfer_pair_id in card_pairs and tx.transfer_pair_id is not None:
            continue
        if _on_card(tx) and (tx.type == "credit" or tx.date <= today):
            continue
        connected = tx.account is not None and tx.account.connection_id is not None
        # A provider balance may already hold its own pending rows; never a placeholder of ours.
        if connected and tx.status == "pending" and tx.date <= today and tx.source != "recurring":
            continue
        account_currency = tx.account.currency if tx.account else tx.currency
        signed = Decimal(str(_signed_transaction_amount(tx)))
        amount: Optional[Decimal]
        amount, _ = await convert_or_none(session, signed, account_currency, base_currency, today)
        if amount is None:
            found.unconverted_currencies.add(account_currency)
            continue
        if not amount:
            continue
        found.rows.append(ScheduledRow(
            on=max(tx.date, tomorrow),
            name=tx.description or "Scheduled",
            name_key=normalize(tx.payee or tx.description).lower(),
            direction=FlowDirectionEnum.INFLOW if amount > 0 else FlowDirectionEnum.OUTFLOW,
            amount=abs(amount),
        ))
    return found


def _on_card(tx) -> bool:
    return tx.account is not None and tx.account.type == CARD_TYPE


async def uncovered_account_ids(session: AsyncSession, workspace_id: uuid.UUID, covered: set[Any]) -> set[uuid.UUID]:
    """Every account of the workspace the forecast does not follow, closed ones included."""
    from app.models.account import Account

    result = await session.execute(select(Account.id).where(Account.workspace_id == workspace_id))
    return {account_id for account_id in result.scalars().all() if account_id not in covered}


async def materialized_occurrences(session, workspace_id, rule_ids, today: date, end_date: date):
    """(rule id, date) of occurrences already stored as rows, as the dashboard skips them."""
    from app.services.dashboard_service import _materialized_recurring_occurrences

    return await _materialized_recurring_occurrences(
        session, workspace_id, set(rule_ids), today - timedelta(days=3), end_date + timedelta(days=1),
    )
