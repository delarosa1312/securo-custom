"""Projection consistency (F-006, issue #8): helpers the projection views share.

Pure where it can be: plain rows in, rows out. See `docs/fork/PROJECTIONS.md`
for what each view counts and which differences are intended.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from sqlalchemy import or_, select

from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction


def _key(projection: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in projection.items()))


def only_in(wider: Iterable[dict], narrower: Iterable[dict]) -> list[dict]:
    """Projections of ``wider`` that ``narrower`` lacks, counting repeats.

    Both lists come from `_get_recurring_projections` over the same window, the
    wider one with transfer-like rules: the difference is those rules' parts.
    """
    left = Counter(_key(p) for p in narrower)
    extra = []
    for projection in wider:
        key = _key(projection)
        if left[key]:
            left[key] -= 1
        else:
            extra.append(projection)
    return extra


def on_open_account(stmt):
    """Restrict a `select(RecurringTransaction)` to rules whose account is open.

    A closed account is in no balance, and forecast rows from closed accounts
    are already left out (`_get_forecast_transactions`), so its rules are too.
    A legacy rule without an account keeps its old treatment.
    """
    closed = select(Account.id).where(Account.is_closed.is_(True))
    return stmt.where(
        or_(RecurringTransaction.account_id.is_(None), RecurringTransaction.account_id.not_in(closed))
    )


async def scheduled_balance_moves(
    session,
    workspace_id,
    start,
    end,
    account_ids,
    primary_currency: str,
    *,
    skip_account_ids=frozenset(),
) -> dict:
    """Virtual moves in ``[start, end)`` per day, in the primary currency.

    What the dashboard card adds to the balance besides real rows: recurring
    occurrences (transfer-like rules included: they move the account) and, with
    no account filter, open invoice claims (a claim has no account yet).
    """
    from decimal import Decimal

    from app.services.dashboard_service import _get_recurring_projections
    from app.services.fx_rate_service import convert

    moves: dict = {}
    if start >= end:
        return moves
    # A rule repeats the same amount: convert each (amount, currency) once, not per occurrence.
    converted_by_key: dict = {}
    for proj in await _get_recurring_projections(
        session, workspace_id, start, end, account_ids, include_transfer_like=True,
    ):
        if proj["account_id"] in skip_account_ids:
            continue
        key = (str(proj["amount"]), proj["currency"])
        if key not in converted_by_key:
            converted_by_key[key], _ = await convert(session, Decimal(key[0]), key[1], primary_currency)
        converted = converted_by_key[key]
        signed = float(converted) if proj["type"] == "credit" else -float(converted)
        moves[proj["date"]] = moves.get(proj["date"], 0.0) + signed
    if account_ids is None:
        for due, signed in (await claim_moves(session, workspace_id, start, end, primary_currency)).items():
            moves[due] = moves.get(due, 0.0) + signed
    return moves


async def claim_moves(session, workspace_id, start, end, primary_currency: str) -> dict:
    """Open invoice claims due in ``[start, end)``, signed, per day, in the primary currency."""
    from app.services import invoice_forecast_service
    from app.services.fx_rate_service import convert

    moves: dict = {}
    if start >= end:
        return moves
    for claim in await invoice_forecast_service.claims_in_range(session, workspace_id, start, end):
        converted, _ = await convert(session, claim.signed, claim.currency, primary_currency)
        moves[claim.due_date] = moves.get(claim.due_date, 0.0) + float(converted)
    return moves


async def overdue_pending(session, workspace_id, today, account_ids) -> list:
    """Pending rows dated today or earlier that the current balance does not hold yet.

    The views count them from tomorrow, the first projected day. A connected
    account's own pending rows are left out: its provider balance may already
    hold them (the dashboard card's rule); a placeholder generated from a
    schedule is not in it and counts.
    """
    from datetime import date, timedelta

    from app.services.dashboard_service import _get_forecast_transactions

    rows = []
    for tx in await _get_forecast_transactions(session, workspace_id, date.min, today + timedelta(days=1), account_ids):
        if tx.status != "pending" or tx.is_ignored or (tx.category and tx.category.is_ignored):
            continue
        if tx.account is not None and tx.account.connection_id is not None and tx.source != "recurring":
            continue
        rows.append(tx)
    return rows


async def calendar_row_moves(session, workspace_id, today, grid_start, grid_end, account_ids, signed_delta):
    """The calendar's balance moves from real forecast rows: (seed correction, moves per day).

    The calendar lists a row on its reporting date, but a balance moves on the
    row's own date, as in every other view; a row billed after the grid still
    moves it. ``signed_delta`` is the calendar's own conversion (an awaitable per row).
    The seed (`_balance_at` with pending rows) holds overdue manual rows on their
    own dates; they move tomorrow instead. A connected account's placeholder was
    taken out of the seed as if the provider held it, so it goes back first.
    """
    from datetime import timedelta

    from app.services.dashboard_service import _get_forecast_transactions

    tomorrow = today + timedelta(days=1)
    seed = 0.0
    moves: dict = {}
    overdue = await overdue_pending(session, workspace_id, today, account_ids)
    overdue_delta = 0.0
    for tx in overdue:
        delta = await signed_delta(tx)
        if tx.account is not None and tx.account.connection_id is not None:
            if tx.date >= grid_start and grid_start <= today:
                seed += delta
        elif tx.date < grid_start:
            seed -= delta
        overdue_delta += delta
    if tomorrow < grid_start:
        seed += overdue_delta
    elif tomorrow < grid_end and overdue_delta:
        moves[tomorrow] = overdue_delta
    overdue_ids = {tx.id for tx in overdue}
    for tx in await _get_forecast_transactions(session, workspace_id, grid_start, grid_end, account_ids):
        if tx.id in overdue_ids or tx.is_ignored or (tx.category and tx.category.is_ignored):
            continue
        moves[tx.date] = moves.get(tx.date, 0.0) + await signed_delta(tx)
    return seed, moves


async def calendar_claim_moves(session, workspace_id, today, grid_start, grid_end, primary_currency: str):
    """Open invoice claims for a calendar grid: (seed for the days between today and the grid, listed claims).

    Each listed claim is ``(due date, signed amount in the primary currency, calendar item)``: the calendar
    lists every claim it counts (Q13), as "Invoice <number> (expected)".
    """
    from datetime import timedelta

    from app.services import invoice_forecast_service
    from app.services.fx_rate_service import convert

    first_projected = today + timedelta(days=1)
    seed = sum((await claim_moves(session, workspace_id, first_projected, grid_start, primary_currency)).values())
    start = max(grid_start, first_projected)
    if start >= grid_end:
        return seed, []
    claims = await invoice_forecast_service.claims_in_range(session, workspace_id, start, grid_end)
    numbers = await _invoice_numbers(session, workspace_id, {c.invoice_id for c in claims if c.invoice_id})
    listed = []
    for claim in claims:
        converted, _ = await convert(session, claim.signed, claim.currency, primary_currency)
        number = numbers.get(claim.invoice_id)
        listed.append((claim.due_date, float(converted), _claim_item(claim, abs(float(converted)), number)))
    return seed, listed


def invoice_label(number) -> str:
    """The English label; the web app shows its own translation from ``invoice_number``."""
    return f"Invoice {number} (expected)" if number else "Invoice (expected)"


def _claim_item(claim, amount_primary: float, number):
    from app.schemas.transaction_calendar import TransactionCalendarItem

    return TransactionCalendarItem(
        kind="projected",
        date=claim.due_date,
        description=invoice_label(number),
        amount=float(claim.amount),
        amount_primary=amount_primary,
        currency=claim.currency,
        type="credit" if claim.direction == "receivable" else "debit",
        source="invoice",
        invoice_id=claim.invoice_id,
        invoice_number=number,
    )


async def _invoice_numbers(session, workspace_id, invoice_ids) -> dict:
    """Each invoice's number as a person reads it (`invoice_document`): an import's own reference, or the prefix
    it was issued with plus the sequence. Reads the settings row without creating it."""
    from app.models.invoice import Invoice, InvoiceSettings

    if not invoice_ids:
        return {}
    invoices = (await session.execute(select(Invoice).where(Invoice.id.in_(invoice_ids)))).scalars().all()
    settings = (await session.execute(
        select(InvoiceSettings).where(InvoiceSettings.workspace_id == workspace_id)
    )).scalar_one_or_none()
    live_prefix = (settings.number_prefix if settings else None) or ""
    numbers = {}
    for invoice in invoices:
        if invoice.origin == "imported":
            numbers[invoice.id] = invoice.external_number or None
        elif invoice.number is not None:
            prefix = (invoice.snapshot.get("number_prefix") if invoice.snapshot else live_prefix) or ""
            numbers[invoice.id] = f"{prefix}{invoice.number}"
    return numbers
