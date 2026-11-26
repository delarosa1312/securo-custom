#!/usr/bin/env python3
"""Load the synthetic E2E data set into a fresh local stack through the app's own REST API.

Synthetic only: invented names and round amounts, a throwaway login for the local stack. Covers what the
forecast features need: accounts of each kind, posted history, pending rows (past and future), recurring
schedules, an installment series, transfers (past and future) and budgets (recurring and month-specific).
Dates are relative to today, so the Cash Flow report always has a past and a future.
Stdlib only. Usage: seed.py --base-url http://127.0.0.1:18000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

EMAIL = "e2e@example.com"
PASSWORD = "Synthetic-E2E-2026"  # local stack only; also in fork/e2e/README.md
CURRENCY = "USD"


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body: object = None, form: dict | None = None) -> object:
        headers = {"Accept": "application/json"}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            sys.exit(f"{method} {path} -> {err.code}: {err.read().decode()[:500]}")
        return json.loads(raw) if raw else None


def month_day(today: date, months_back: int, day: int) -> date:
    y, m = today.year, today.month - months_back
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, day)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    args = parser.parse_args()
    api = Api(args.base_url)
    today = date.today()

    status = api.call("GET", "/api/setup/status")
    if not isinstance(status, dict) or status.get("has_users", True):
        sys.exit("this stack is already set up; seed a fresh one: fork/tools/stack.sh down && fork/tools/stack.sh up")
    api.call("POST", "/api/setup/create-admin",
             {"email": EMAIL, "password": PASSWORD, "currency": CURRENCY, "name": "E2E User", "language": "en"})
    login = api.call("POST", "/api/auth/login", form={"username": EMAIL, "password": PASSWORD})
    assert isinstance(login, dict) and "access_token" in login, login
    api.token = login["access_token"]
    # The seed user has finished the first-login tour, as the app records it when the tour ends.
    me = api.call("GET", "/api/users/me")
    prefs = {**((me or {}).get("preferences") or {}), "onboarding_completed": True}  # type: ignore[union-attr]
    api.call("PATCH", "/api/users/me", {"preferences": prefs})

    def account(name: str, kind: str, balance: str, **extra) -> str:
        body = {"name": name, "type": kind, "balance": balance, "currency": CURRENCY, **extra}
        return api.call("POST", "/api/accounts", body)["id"]  # type: ignore[index]

    checking = account("Synthetic Checking", "checking", "4200.00")
    savings = account("Synthetic Savings", "savings", "12000.00")
    card = account("Synthetic Card", "credit_card", "0.00",
                   credit_limit="5000.00", statement_close_day=5, payment_due_day=15)

    existing = {c["name"]: c["id"] for c in api.call("GET", "/api/categories")}  # type: ignore[union-attr]

    def category(name: str) -> str:
        if name not in existing:
            existing[name] = api.call("POST", "/api/categories", {"name": name})["id"]  # type: ignore[index]
        return existing[name]

    salary, rent, groceries = category("Salary"), category("Rent"), category("Groceries")
    dining, transport, subscriptions = category("Restaurants"), category("Transport"), category("Subscriptions")
    electronics, insurance = category("Electronics"), category("Insurance")

    counts: dict[str, int] = {}

    def tx(acct: str, desc: str, amount: str, when: date, kind: str, cat: str | None, status: str = "posted"):
        api.call("POST", "/api/transactions", {
            "account_id": acct, "description": desc, "amount": amount, "date": when.isoformat(),
            "type": kind, "category_id": cat, "currency": CURRENCY, "status": status,
        })
        counts[f"transactions ({status})"] = counts.get(f"transactions ({status})", 0) + 1

    # Posted history: three full months plus this month up to today.
    for back in (3, 2, 1, 0):
        for day, desc, amount, kind, acct, cat in (
            (5, "Synthetic Employer payroll", "5000.00", "credit", checking, salary),
            (10, "Synthetic Landlord rent", "1500.00", "debit", checking, rent),
            (3, "Synthetic Market", "135.40", "debit", checking, groceries),
            (17, "Synthetic Market", "128.75", "debit", checking, groceries),
            (8, "Synthetic Bistro", "62.00", "debit", card, dining),
            (21, "Synthetic Transit pass", "90.00", "debit", card, transport),
        ):
            when = month_day(today, back, day)
            if when <= today:
                tx(acct, desc, amount, when, kind, cat)

    # Pending rows: one not yet cleared by the bank, two scheduled in the future.
    tx(card, "Synthetic Bistro (pending)", "48.20", today - timedelta(days=1), "debit", dining, "pending")
    tx(checking, "Synthetic Insurer premium", "230.00", today + timedelta(days=7), "debit", insurance, "pending")
    tx(checking, "Synthetic Freelance invoice", "800.00", today + timedelta(days=20), "credit", salary, "pending")

    # Installments: ten monthly parcels on the card, two of them already past.
    api.call("POST", "/api/transactions/installments", {
        "base": {"account_id": card, "description": "Synthetic Laptop", "amount": "150.00",
                 "date": month_day(today, 2, 12).isoformat(), "type": "debit", "category_id": electronics,
                 "currency": CURRENCY},
        "installments": 10, "frequency": "monthly", "first_installment_status": "posted",
    })
    counts["installment series (10 parcels)"] = 1

    # Transfers: checking to savings, two past and one future.
    for when in (month_day(today, 2, 6), month_day(today, 1, 6), today + timedelta(days=14)):
        api.call("POST", "/api/transactions/transfer", {
            "from_account_id": checking, "to_account_id": savings, "amount": "500.00",
            "date": when.isoformat(), "description": "Synthetic savings transfer"})
        counts["transfers"] = counts.get("transfers", 0) + 1

    # Recurring schedules, starting next month so they project forward without duplicating history.
    start = month_day(today, -1, 1)
    for desc, amount, kind, acct, cat, dom in (
        ("Synthetic Employer payroll", "5000.00", "credit", checking, salary, 5),
        ("Synthetic Landlord rent", "1500.00", "debit", checking, rent, 10),
        ("Synthetic Streaming", "15.99", "debit", card, subscriptions, 18),
        ("Synthetic Gym", "45.00", "debit", checking, subscriptions, 25),
    ):
        api.call("POST", "/api/recurring-transactions", {
            "description": desc, "amount": amount, "currency": CURRENCY, "type": kind, "frequency": "monthly",
            "day_of_month": dom, "start_date": start.replace(day=dom).isoformat(), "account_id": acct,
            "category_id": cat, "auto_generate": False})
        counts["recurring schedules"] = counts.get("recurring schedules", 0) + 1

    # Budgets: recurring from three months ago, one month-specific override, one month-only budget.
    first_month = month_day(today, 3, 1)
    for cat, amount in ((groceries, "600.00"), (dining, "250.00"), (transport, "120.00")):
        api.call("POST", "/api/budgets", {"category_id": cat, "amount": amount,
                                          "month": first_month.isoformat(), "is_recurring": True})
    api.call("POST", "/api/budgets", {"category_id": groceries, "amount": "700.00",
                                      "month": month_day(today, -1, 1).isoformat(), "is_recurring": False})
    api.call("POST", "/api/budgets", {"category_id": insurance, "amount": "230.00",
                                      "month": month_day(today, 0, 1).isoformat(), "is_recurring": False})
    counts["budgets"] = 5

    print(f"seeded {args.base_url} as {EMAIL}")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
