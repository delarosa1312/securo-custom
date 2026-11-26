"""F-001 calculator: pure, no database. Synthetic accounts only."""

import uuid
from decimal import Decimal

import pytest

from app.fork.available_balance import (
    check_available_setting,
    compute_available_balance,
    counts_as_available,
)


def _acc(name, type_, balance, currency="BRL", *, include=True, closed=False, primary=None, fx_fallback=False):
    return {
        "id": uuid.uuid4(),
        "name": name,
        "type": type_,
        "currency": currency,
        "current_balance": balance,
        "balance_primary": primary,
        "fx_fallback": fx_fallback,
        "is_closed": closed,
        "include_in_available": include,
    }


def _worked_example():
    return [
        _acc("Current account", "checking", 500.0),
        _acc("Cash", "wallet", 100.0),
        _acc("Reserve", "savings", 1000.0, include=False),
        _acc("Card", "credit_card", -300.0),
    ]


def test_worked_example_leaves_the_reserve_out():
    result = compute_available_balance(_worked_example(), "BRL")
    assert result.total_primary == Decimal("600.00")
    assert result.by_currency == {"BRL": Decimal("600.00")}
    assert [a["name"] for a in result.included] == ["Current account", "Cash"]
    assert [a["name"] for a in result.excluded] == ["Reserve"]
    assert result.fx_fallback is False
    assert result.unconverted_currencies == []


def test_every_account_on_equals_the_upstream_type_filter():
    accounts = _worked_example()
    accounts[2]["include_in_available"] = True
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("1600.00")
    assert result.excluded == []


@pytest.mark.parametrize("type_", ["credit_card", "loan", "investment"])
def test_other_types_never_count_whatever_is_stored(type_):
    for include in (True, False):
        result = compute_available_balance([_acc("Other", type_, 250.0, include=include)], "BRL")
        assert result.total_primary == Decimal("0.00")
        assert result.included == [] and result.excluded == []


def test_closed_accounts_never_count_and_are_not_listed_as_excluded():
    accounts = [
        _acc("Old checking", "checking", 80.0, closed=True),
        _acc("Old reserve", "savings", 900.0, closed=True, include=False),
    ]
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("0.00")
    assert result.included == [] and result.excluded == []


def test_missing_key_counts_as_included():
    account = _acc("Legacy", "checking", 10.0)
    del account["include_in_available"]
    assert counts_as_available(account)


def test_two_currencies_with_a_rate_sum_the_converted_figures():
    accounts = [
        _acc("Current account", "checking", 500.0),
        _acc("Travel wallet", "wallet", 50.0, "USD", primary=250.0),
        _acc("USD reserve", "savings", 400.0, "USD", include=False, primary=2000.0),
    ]
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("750.00")
    assert result.by_currency == {"BRL": Decimal("500.00"), "USD": Decimal("50.00")}
    assert result.fx_fallback is False


def test_two_currencies_without_a_rate_add_the_native_amount_and_say_so():
    # Same rule as the dashboard headline (`balance_primary ?? current_balance`).
    accounts = [
        _acc("Current account", "checking", 500.0),
        _acc("Travel wallet", "wallet", 50.0, "USD", fx_fallback=True),
    ]
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("550.00")
    assert result.fx_fallback is True
    assert result.unconverted_currencies == ["USD"]


def test_an_excluded_account_without_a_rate_does_not_raise_the_flag():
    accounts = [
        _acc("Current account", "checking", 500.0),
        _acc("USD reserve", "savings", 400.0, "USD", include=False, fx_fallback=True),
    ]
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("500.00")
    assert result.fx_fallback is False
    assert result.unconverted_currencies == []


def test_cents_add_up_exactly():
    accounts = [_acc("A", "checking", 0.1), _acc("B", "wallet", 0.2), _acc("C", "savings", 1234567.89)]
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("1234568.19")


def test_negative_balances_count():
    result = compute_available_balance([_acc("Overdrawn", "checking", -42.5), _acc("Cash", "wallet", 10.0)], "BRL")
    assert result.total_primary == Decimal("-32.50")


def test_all_liquid_accounts_off_gives_zero():
    accounts = [_acc("A", "checking", 10.0, include=False), _acc("B", "wallet", 5.0, include=False)]
    result = compute_available_balance(accounts, "BRL")
    assert result.total_primary == Decimal("0.00")
    assert result.by_currency == {}
    assert len(result.excluded) == 2


def test_no_accounts_gives_zero():
    result = compute_available_balance([], "BRL")
    assert result.total_primary == Decimal("0.00")


class TestCheckAvailableSetting:
    def test_absent_is_fine(self):
        check_available_setting("credit_card", {"name": "x"})

    def test_null_is_refused(self):
        with pytest.raises(ValueError):
            check_available_setting("checking", {"include_in_available": None})

    @pytest.mark.parametrize("type_", ["checking", "savings", "wallet"])
    def test_off_on_a_liquid_type_is_fine(self, type_):
        check_available_setting(type_, {"include_in_available": False})

    @pytest.mark.parametrize("type_", ["credit_card", "loan", "investment"])
    def test_off_on_another_type_is_refused(self, type_):
        with pytest.raises(ValueError, match="checking, savings and wallet"):
            check_available_setting(type_, {"include_in_available": False})

    def test_on_is_accepted_anywhere(self):
        check_available_setting("credit_card", {"include_in_available": True})
