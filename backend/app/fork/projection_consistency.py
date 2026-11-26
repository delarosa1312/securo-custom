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
    for proj in await _get_recurring_projections(
        session, workspace_id, start, end, account_ids, include_transfer_like=True,
    ):
        if proj["account_id"] in skip_account_ids:
            continue
        converted, _ = await convert(session, Decimal(str(proj["amount"])), proj["currency"], primary_currency)
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
