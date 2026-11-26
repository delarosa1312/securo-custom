"""F-006: every projection view gives the same balance for the same day (issue #8). Synthetic data only.

One base (checking 1,000.00, savings 500.00) plus one component at a time;
each view is read at the same seven dates with all accounts and with checking only.
A component lists the balance moves it should cause; a view may leave one out
only where `docs/fork/PROJECTIONS.md` says so (``LEFT_OUT``).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import admin_service
from tests.fork import _budget_data as data
from tests.fork import _projection_data as pdata
from tests.fork import _projection_views as views

D = date
DATES = (
    D(2026, 10, 31), D(2026, 11, 10), D(2026, 11, 20), D(2026, 11, 25), D(2026, 11, 30), D(2026, 12, 1),
    D(2026, 12, 31),
)
BASE = {"all": Decimal("1500.00"), "checking": Decimal("1000.00")}

# component -> [(date, amount, account)]; "checking" moves show in both scopes, anything else only in "all".
MOVES: dict[str, list[tuple[date, str, str]]] = {
    "base": [],
    "pending_future": [(D(2026, 11, 25), "-100", "checking")],
    "future_posted_installment": [(D(2026, 12, 5), "-80", "checking")],
    "recurring": [(D(2026, 11, 28), "-30", "checking"), (D(2026, 12, 28), "-30", "checking")],
    "recurring_materialized": [(D(2026, 11, 28), "-30", "checking"), (D(2026, 12, 28), "-30", "checking")],
    # Q12: an occurrence dated today or earlier counts only once it is a row; the next one counts on its date.
    "recurring_overdue": [(D(2026, 12, 15), "-30", "checking")],
    "recurring_due_today": [(D(2026, 12, 20), "-30", "checking")],
    "transfer_future": [(D(2026, 12, 3), "-200", "checking"), (D(2026, 12, 3), "200", "savings")],
    "transfer_past": [(D(2026, 11, 5), "-300", "checking"), (D(2026, 11, 5), "300", "savings")],
    "recurring_transfer_like": [(D(2026, 11, 25), "-500", "checking"), (D(2026, 12, 25), "-500", "checking")],
    "ignored_rule": [],
    "closed_account_rule": [],
    "investment_account_rule": [(D(2026, 11, 26), "40", "broker"), (D(2026, 12, 26), "40", "broker")],
    "excluded_from_pnl_past": [(D(2026, 11, 5), "-60", "checking")],
    "card_purchase_and_installment": [
        (D(2026, 11, 18), "-120", "card"), (D(2026, 11, 25), "-70", "card"), (D(2026, 12, 18), "-120", "card"),
    ],
    "invoice_claim": [(D(2026, 11, 27), "400", "claim")],
    "foreign_pending": [(D(2026, 9, 1), "500", "usd"), (D(2026, 11, 25), "-50", "usd")],
    # Not in the balance yet: counted from tomorrow, the first projected day.
    "pending_overdue_and_today": [(D(2026, 11, 21), "-50", "checking"), (D(2026, 11, 21), "-20", "checking")],
    # The provider's 300 holds its own pending 15; our recurring placeholder of 25 is not in it.
    "connected_pending": [(D(2026, 9, 1), "300", "bank"), (D(2026, 11, 21), "-25", "bank")],
    "month_edges": [
        (D(2026, 11, 30), "-20", "checking"), (D(2026, 11, 30), "-5", "checking"),
        (D(2026, 12, 1), "-10", "checking"), (D(2026, 12, 1), "-7", "checking"), (D(2026, 12, 30), "-20", "checking"),
    ],
    # Billed or reported after their own date (a manual bill date, a reporting override): balances move on the date.
    "bill_date_and_override": [(D(2026, 11, 25), "-70", "card"), (D(2026, 11, 26), "-100", "checking")],
}

# Moves a view leaves out by design (docs/fork/PROJECTIONS.md), keyed by (component, view).
LEFT_OUT = {
    # Liquid accounts only: an investment account is not money to spend.
    ("investment_account_rule", "predictive"): {"broker"},
    # A claim has no account until it is paid.
    ("invoice_claim", "predictive"): {"claim"},
}
# A connected account's past days depend on what its provider figure holds, which each view guesses
# differently (docs/fork/PROJECTIONS.md, known differences): compared from tomorrow on.
FROM_TOMORROW = {"connected_pending"}

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


# The Cash Flow is a bill-date cash view: a row with a bill date (every card row in accrual mode, a manual bill
# date in either mode) leaves on that date (docs/fork/PROJECTIONS.md). Only that view is skipped for those rows.
CASH_FLOW_BILL_DATE = {("bill_date_and_override", "cash"), ("bill_date_and_override", "accrual"),
                       ("card_purchase_and_installment", "accrual")}
ACCRUAL_CASES = list(MOVES)


@pytest.mark.parametrize("component", list(MOVES))
@pytest.mark.parametrize("scope", ["all", "checking"])
@pytest.mark.asyncio
async def test_views_reconcile(session, test_user, test_workspace, monkeypatch, component, scope):
    await _reconcile(session, test_user, test_workspace, monkeypatch, component, scope)


@pytest.mark.parametrize("component", ACCRUAL_CASES)
@pytest.mark.asyncio
async def test_views_reconcile_in_accrual_mode(session, test_user, test_workspace, monkeypatch, component):
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
    await _reconcile(session, test_user, test_workspace, monkeypatch, component, "all", mode="accrual")


async def _reconcile(session, test_user, test_workspace, monkeypatch, component, scope, mode="cash"):
    data.pin_today(monkeypatch)
    base = await pdata.build(session, test_user, test_workspace, component)
    account_ids = None if scope == "all" else [base.checking.id]
    wrong = []
    for on in DATES:
        if component in FROM_TOMORROW and on <= data.TODAY:
            continue
        got = await views.read_all(session, test_workspace, test_user, on, account_ids)
        for view, value in got.items():
            if value is None or (view == "cash_flow" and (component, mode) in CASH_FLOW_BILL_DATE):
                continue
            want = expected(component, scope, view, on)
            if value != want:
                wrong.append(f"{on} {view}: {value} != {want}")
    assert not wrong, "\n".join(wrong)
