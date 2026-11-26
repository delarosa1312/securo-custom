"""F-006 reconciliation harness: every projection view read with aligned parameters.

Each reader returns the projected balance at the end of ``on`` in the user's
primary currency, rounded to the cent, or ``None`` when that view cannot show
that day for those parameters (for example the balance history chart hides
the rest of the current month, and the predictive forecast has no account
filter). Synthetic data only.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from app.core import app_clock
from app.services import dashboard_service, report_service, transaction_calendar_service
from app.services.cashflow_forecast_service import generate_cashflow_forecast

CENT = Decimal("0.01")
VIEWS = ("dashboard", "balance_history", "cash_flow", "calendar", "predictive")


def cents(value) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value)).quantize(CENT)


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


async def dashboard(session, ws, user, on: date, account_ids=None) -> Optional[Decimal]:
    """Summary card "projected balance": defined at the end of the month it is asked for."""
    if on != _month_end(on):
        return None
    summary = await dashboard_service.get_summary(
        session, ws.id, user.id, month=on.replace(day=1), account_ids=account_ids,
    )
    return cents(summary.projected_balance_primary)


async def balance_history(session, ws, user, on: date, account_ids=None) -> Optional[Decimal]:
    history = await dashboard_service.get_balance_history(
        session, ws.id, user.id, month=on.replace(day=1), account_ids=account_ids,
    )
    return cents(history.current[on.day - 1].balance)


async def cash_flow(session, ws, user, on: date, account_ids=None, **options) -> Optional[Decimal]:
    today = app_clock.app_today()
    months = max((on.year - today.year) * 12 + on.month - today.month, 1)
    report = await report_service.get_cash_flow_report(
        session, ws.id, user.id, months=months, interval="daily", account_ids=account_ids, **options,
    )
    label = on.isoformat()
    point = next((p for p in report.trend if p.date == label), None)
    return cents(point.value) if point else None


async def calendar(session, ws, user, on: date, account_ids=None) -> Optional[Decimal]:
    response = await transaction_calendar_service.get_transaction_calendar(
        session, ws.id, user.id, month=on.replace(day=1), account_ids=account_ids,
    )
    day = next(d for d in response.days if d.date == on)
    return cents(day.ending_balance)


async def predictive(session, ws, user, on: date, account_ids=None) -> Optional[Decimal]:
    """Scheduled-only (no discretionary burn); it has no account filter."""
    today = app_clock.app_today()
    if account_ids is not None or on < today:
        return None
    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=(on - today).days, include_discretionary_burn=False,
    )
    return cents(forecast.daily_trajectory[-1].ending_balance)


READERS = {
    "dashboard": dashboard,
    "balance_history": balance_history,
    "cash_flow": cash_flow,
    "calendar": calendar,
    "predictive": predictive,
}


async def read_all(
    session, ws, user, on: date, account_ids: Optional[list[uuid.UUID]] = None,
) -> dict[str, Optional[Decimal]]:
    return {name: await reader(session, ws, user, on, account_ids) for name, reader in READERS.items()}
