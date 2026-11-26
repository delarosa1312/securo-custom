"""F-006: every projection view gives the same balance for the same day (issue #8). Synthetic data only.

One base (checking 1,000.00, savings 500.00) plus one component at a time;
each view is read at the same dates with all accounts and with checking only.
A component lists the balance moves it should cause; a view may leave one out
only where `docs/fork/PROJECTIONS.md` says so (``LEFT_OUT``).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.fork import _budget_data as data
from tests.fork import _projection_data as pdata
from tests.fork import _projection_views as views

D = date
DATES = (D(2026, 10, 31), D(2026, 11, 10), D(2026, 11, 20), D(2026, 11, 25), D(2026, 11, 30), D(2026, 12, 31))
BASE = {"all": Decimal("1500.00"), "checking": Decimal("1000.00")}

# component -> [(date, amount, account)]; "checking" moves show in both scopes, anything else only in "all".
MOVES: dict[str, list[tuple[date, str, str]]] = {
    "base": [],
    "pending_future": [(D(2026, 11, 25), "-100", "checking")],
    "future_posted_installment": [(D(2026, 12, 5), "-80", "checking")],
    "recurring": [(D(2026, 11, 28), "-30", "checking"), (D(2026, 12, 28), "-30", "checking")],
    "recurring_materialized": [(D(2026, 11, 28), "-30", "checking"), (D(2026, 12, 28), "-30", "checking")],
    "transfer_future": [(D(2026, 12, 3), "-200", "checking"), (D(2026, 12, 3), "200", "savings")],
    "transfer_past": [(D(2026, 11, 5), "-300", "checking"), (D(2026, 11, 5), "300", "savings")],
    "recurring_transfer_like": [(D(2026, 11, 25), "-500", "checking"), (D(2026, 12, 25), "-500", "checking")],
    "ignored_rule": [],
    "closed_account_rule": [],
    "investment_account_rule": [(D(2026, 11, 26), "40", "broker"), (D(2026, 12, 26), "40", "broker")],
    "excluded_from_pnl_past": [(D(2026, 11, 5), "-60", "checking")],
    "card_purchase_and_installment": [(D(2026, 11, 18), "-120", "card"), (D(2026, 12, 18), "-120", "card")],
    "invoice_claim": [(D(2026, 11, 27), "400", "claim")],
    "foreign_pending": [(D(2026, 9, 1), "500", "usd"), (D(2026, 11, 25), "-50", "usd")],
}

# Moves a view leaves out by design (docs/fork/PROJECTIONS.md), keyed by (component, view).
LEFT_OUT = {
    # Liquid accounts only: an investment account is not money to spend.
    ("investment_account_rule", "predictive"): {"broker"},
    # A claim has no account until it is paid.
    ("invoice_claim", "predictive"): {"claim"},
}
# Card debt already owed is not due today: the predictive forecast starts without it.
PREDICTIVE_SKIPS_PAST = {"card"}


def expected(component: str, scope: str, view: str, on: date) -> Decimal:
    today = data.TODAY
    total = BASE[scope]
    left_out = LEFT_OUT.get((component, view), set())
    for when, amount, account in MOVES[component]:
        if when > on or account in left_out:
            continue
        if scope == "checking" and account != "checking":
            continue
        if view == "predictive" and account in PREDICTIVE_SKIPS_PAST and when <= today:
            continue
        total += Decimal(amount)
    return total


@pytest.mark.parametrize("component", list(MOVES))
@pytest.mark.parametrize("scope", ["all", "checking"])
@pytest.mark.asyncio
async def test_views_reconcile(session, test_user, test_workspace, monkeypatch, component, scope):
    data.pin_today(monkeypatch)
    base = await pdata.build(session, test_user, test_workspace, component)
    account_ids = None if scope == "all" else [base.checking.id]
    wrong = []
    for on in DATES:
        got = await views.read_all(session, test_workspace, test_user, on, account_ids)
        for view, value in got.items():
            if value is None:
                continue
            want = expected(component, scope, view, on)
            if value != want:
                wrong.append(f"{on} {view}: {value} != {want}")
    assert not wrong, "\n".join(wrong)
