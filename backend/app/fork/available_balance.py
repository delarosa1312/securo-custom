"""Available balance (F-001): the money that can be spent now.

Open checking, savings and wallet accounts whose `include_in_available` is on.
An account switched off keeps its balance, history, reports and net worth; only
the available figures leave it out. Pure: serialized accounts in, `Decimal` out,
so REST, MCP, the predictive forecast and the dashboard agree on one number.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

AVAILABLE_ACCOUNT_TYPES = frozenset({"checking", "savings", "wallet"})

_CENT = Decimal("0.01")


def counts_as_available(account: Mapping[str, Any]) -> bool:
    """Whether a serialized account is part of the available balance."""
    return (
        account.get("type") in AVAILABLE_ACCOUNT_TYPES
        and not account.get("is_closed", False)
        # A missing key is an account serialized before the column existed.
        and account.get("include_in_available", True) is not False
    )


def check_available_setting(account_type: str, update_data: Mapping[str, Any]) -> None:
    """Refuse an `include_in_available` write that cannot be stored or means nothing.

    `account_type` is the type the account has after the same edit, so a type
    change and the setting can travel together.
    """
    if "include_in_available" not in update_data:
        return
    value = update_data["include_in_available"]
    if value is None:
        raise ValueError("include_in_available must be true or false")
    if value is False and account_type not in AVAILABLE_ACCOUNT_TYPES:
        raise ValueError(
            "Only checking, savings and wallet accounts can be left out of the available balance"
        )


@dataclass(frozen=True)
class AvailableBalance:
    primary_currency: str
    # Exact per-currency sums, no conversion.
    by_currency: dict[str, Decimal]
    # In the primary currency, summed exactly as the dashboard sums its
    # headline: each account's converted balance, or its native balance when
    # no FX rate exists (then `fx_fallback` is true and the currency is listed).
    total_primary: Decimal
    fx_fallback: bool
    unconverted_currencies: list[str]
    included: list[Mapping[str, Any]] = field(default_factory=list)
    excluded: list[Mapping[str, Any]] = field(default_factory=list)


def _money(value: Any) -> Decimal:
    # Serialized balances are floats rounded to cents; str() keeps their digits.
    return Decimal(str(value or 0)).quantize(_CENT)


def compute_available_balance(
    accounts: Iterable[Mapping[str, Any]], primary_currency: str
) -> AvailableBalance:
    """Available balance of serialized accounts (`account_service.serialize_account`
    output with `balance_primary` and `fx_fallback` already filled by the API)."""
    by_currency: dict[str, Decimal] = {}
    total_primary = Decimal("0.00")
    unconverted: set[str] = set()
    fx_fallback = False
    included: list[Mapping[str, Any]] = []
    excluded: list[Mapping[str, Any]] = []

    for account in accounts:
        if not counts_as_available(account):
            if (
                account.get("type") in AVAILABLE_ACCOUNT_TYPES
                and not account.get("is_closed", False)
            ):
                excluded.append(account)
            continue
        included.append(account)
        native = _money(account.get("current_balance"))
        currency = account["currency"]
        by_currency[currency] = by_currency.get(currency, Decimal("0.00")) + native
        if currency == primary_currency:
            total_primary += native
        elif account.get("balance_primary") is not None:
            total_primary += _money(account["balance_primary"])
        else:
            total_primary += native
            unconverted.add(currency)
        if account.get("fx_fallback"):
            fx_fallback = True

    return AvailableBalance(
        primary_currency=primary_currency,
        by_currency=by_currency,
        total_primary=total_primary,
        fx_fallback=fx_fallback,
        unconverted_currencies=sorted(unconverted),
        included=included,
        excluded=excluded,
    )
