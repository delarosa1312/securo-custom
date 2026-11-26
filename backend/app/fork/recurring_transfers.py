"""Recurring transfers between the household's own accounts (F-004).

A transfer schedule is an upstream `recurring_transactions` row with `destination_account_id`
set: `account_id` is the source, `type` is debit, no category. Generation writes one transfer
pair per occurrence (`recurring_generation.py`); projections see the schedule as two legs, a
debit on the source and a credit on the destination, so every balance view moves both accounts
and the P&L views leave it out like a real transfer pair.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Optional, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fork.available_balance import counts_as_available
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction

CROSS_CURRENCY_NEEDS_AMOUNT = (
    "Transfers between accounts in different currencies need the amount received in the "
    "destination account (destination_amount)"
)
SAME_CURRENCY_NO_AMOUNT = "Destination amount must be absent for same-currency transfers"
SAME_ACCOUNT = "Cannot transfer to the same account"
NO_CATEGORY = "A transfer schedule has no category or split across categories"
DEBIT_ONLY = "A transfer schedule moves money out of the source account: its type is debit"
CLOSED_ACCOUNT = "Transfers cannot use a closed account"
NO_SOURCE_TRANSACTION = "A transfer schedule cannot be made from an existing transaction"
NO_DESTINATION_AMOUNT_WITHOUT_DESTINATION = (
    "destination_amount needs destination_account_id (a transfer schedule)"
)
POSITIVE_AMOUNT = "A transfer's amount must be greater than zero"

# What makes a schedule a valid transfer; an edit touching none of these is not judged again.
_TRANSFER_FIELDS = (
    "account_id", "destination_account_id", "destination_amount", "currency", "type", "category_id",
)


def is_transfer_schedule(rec: Any) -> bool:
    return getattr(rec, "destination_account_id", None) is not None


def currency_mismatch(currency: str) -> str:
    return f"A transfer schedule's currency is its source account's ({currency})"


def touches_accounts(account_ids: Iterable[uuid.UUID]):
    """SQL filter: the schedule's source, or a transfer's destination, is one of ``account_ids``."""
    ids = list(account_ids)
    return or_(
        RecurringTransaction.account_id.in_(ids),
        RecurringTransaction.destination_account_id.in_(ids),
    )


async def _workspace_account(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID, missing: str
) -> Account:
    """The account, in the workspace itself: generation moves only such accounts (`usable`)."""
    account = (
        await session.execute(
            select(Account).where(Account.id == account_id, Account.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if account is None:
        raise ValueError(missing)
    return account


def _check_amount(amount: Any) -> None:
    if amount is None or amount <= 0:
        raise ValueError(POSITIVE_AMOUNT)


async def _check(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    account_id: Optional[uuid.UUID],
    destination_account_id: uuid.UUID,
    destination_amount: Optional[Decimal],
    currency: Optional[str],
    type_: Optional[str],
    category_id: Optional[uuid.UUID],
    has_lines: bool,
) -> None:
    if account_id is None:
        raise ValueError("account_id is required")
    if account_id == destination_account_id:
        raise ValueError(SAME_ACCOUNT)
    if type_ != "debit":
        raise ValueError(DEBIT_ONLY)
    if category_id is not None or has_lines:
        raise ValueError(NO_CATEGORY)
    source = await _workspace_account(session, workspace_id, account_id, "Source account not found")
    destination = await _workspace_account(
        session, workspace_id, destination_account_id, "Destination account not found"
    )
    if source.is_closed or destination.is_closed:
        raise ValueError(CLOSED_ACCOUNT)
    if (currency or "").upper() != (source.currency or "").upper():
        raise ValueError(currency_mismatch(source.currency))
    if source.currency != destination.currency:
        if destination_amount is None:
            raise ValueError(CROSS_CURRENCY_NEEDS_AMOUNT)
    elif destination_amount is not None:
        raise ValueError(SAME_CURRENCY_NO_AMOUNT)


async def check_create(session: AsyncSession, workspace_id: uuid.UUID, data: Any) -> None:
    """Refuse a create payload that is not a valid transfer schedule (no-op for other schedules)."""
    if data.destination_account_id is None:
        if data.destination_amount is not None:
            raise ValueError(NO_DESTINATION_AMOUNT_WITHOUT_DESTINATION)
        return
    if data.source_transaction_id is not None:
        raise ValueError(NO_SOURCE_TRANSACTION)
    _check_amount(data.amount)
    await _check(
        session,
        workspace_id,
        account_id=data.account_id,
        destination_account_id=data.destination_account_id,
        destination_amount=data.destination_amount,
        currency=data.currency,
        type_=data.type,
        category_id=data.category_id,
        has_lines=bool(data.category_lines and data.category_lines.lines),
    )


async def check_update(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring: RecurringTransaction,
    update_data: dict[str, Any],
    lines_payload: Any,
) -> None:
    """Refuse an edit that leaves an invalid transfer schedule.

    The schedule is judged as it will be after the edit. Removing the destination (an explicit
    null) turns it back into a plain schedule and clears its destination amount. An edit that
    leaves the transfer fields alone (deactivating, an end date, a new description or amount)
    is not judged again, so it works after one of its accounts was closed.
    """
    if "destination_account_id" in update_data and update_data["destination_account_id"] is None:
        update_data["destination_amount"] = None
    becomes_transfer = update_data.get("destination_account_id", recurring.destination_account_id)
    if becomes_transfer is not None and lines_payload is not None and not lines_payload.lines:
        # Upstream's undone split keeps its largest part's category; a transfer keeps none.
        update_data["category_id"] = None
    after = {
        key: update_data.get(key, getattr(recurring, key))
        for key in (
            "account_id", "destination_account_id", "destination_amount", "currency", "type",
            "category_id",
        )
    }
    if after["destination_account_id"] is None:
        if after["destination_amount"] is not None:
            raise ValueError(NO_DESTINATION_AMOUNT_WITHOUT_DESTINATION)
        return
    if "amount" in update_data:
        _check_amount(update_data["amount"])
    if lines_payload is not None:
        has_lines = bool(lines_payload.lines)
    else:
        has_lines = bool(recurring.has_category_lines)
    if is_transfer_schedule(recurring) and not has_lines and not any(
        key in update_data and update_data[key] != getattr(recurring, key) for key in _TRANSFER_FIELDS
    ):
        return
    await _check(
        session,
        workspace_id,
        account_id=after["account_id"],
        destination_account_id=after["destination_account_id"],
        destination_amount=after["destination_amount"],
        currency=after["currency"],
        type_=after["type"],
        category_id=after["category_id"],
        has_lines=has_lines,
    )


class LegAccount(Protocol):
    id: uuid.UUID
    currency: str


class TransferLeg:
    """One side of a transfer schedule, read by upstream projection loops in place of the schedule.

    Schedule fields (id, dates, frequency, description, ...) come from the schedule; the leg gives
    its own account, direction, amount and currency, and no category. ``transfer_leg`` marks it.
    """

    transfer_leg = True

    def __init__(
        self,
        schedule: RecurringTransaction,
        account: Any,
        counterpart: LegAccount,
        type_: str,
        amount: Decimal,
    ) -> None:
        self._schedule = schedule
        self.account = account
        self.account_id = account.id
        self.counterpart_account_id = counterpart.id
        self.type = type_
        self.amount = amount
        self.currency = account.currency
        self.category_id = None
        self.category = None
        self.has_category_lines = False
        self.category_lines: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._schedule, name)


def is_transfer_leg(item: Any) -> bool:
    return bool(getattr(item, "transfer_leg", False))


def legs_of(
    schedule: RecurringTransaction, source: LegAccount, destination: LegAccount
) -> list[TransferLeg]:
    received = (
        schedule.destination_amount
        if schedule.destination_amount is not None
        else schedule.amount
    )
    return [
        TransferLeg(schedule, source, destination, "debit", schedule.amount),
        TransferLeg(schedule, destination, source, "credit", received),
    ]


def usable(schedule: RecurringTransaction, account: Optional[Account]) -> bool:
    """An account a transfer occurrence may move: open and in the schedule's workspace."""
    return (
        account is not None
        and not account.is_closed
        and account.workspace_id == schedule.workspace_id
    )


async def expand_legs(
    session: AsyncSession,
    schedules: Sequence[Any],
    account_ids: Optional[Iterable[uuid.UUID]] = None,
    *,
    include_transfers: bool = True,
) -> list[Any]:
    """Replace each transfer schedule by its legs; other schedules pass through in order.

    ``include_transfers`` off (the P&L views) drops transfer schedules, as those views drop real
    transfer pairs. With ``account_ids`` only the legs on those accounts stay. A schedule whose
    source or destination is closed, gone or in another workspace projects nothing, because
    generation waits on it too.
    """
    transfers = [rec for rec in schedules if is_transfer_schedule(rec)]
    if not transfers:
        return list(schedules)
    accounts: dict[uuid.UUID, Account] = {}
    if include_transfers:
        ids = {rec.account_id for rec in transfers} | {rec.destination_account_id for rec in transfers}
        result = await session.execute(select(Account).where(Account.id.in_(ids)))
        accounts = {account.id: account for account in result.scalars().all()}
    wanted = set(account_ids) if account_ids is not None else None
    expanded: list[Any] = []
    for rec in schedules:
        if not is_transfer_schedule(rec):
            expanded.append(rec)
            continue
        if not include_transfers:
            continue
        source = accounts.get(rec.account_id)
        destination = accounts.get(rec.destination_account_id)
        if not (usable(rec, source) and usable(rec, destination)):
            continue
        assert source is not None and destination is not None
        expanded.extend(
            leg for leg in legs_of(rec, source, destination)
            if wanted is None or leg.account_id in wanted
        )
    return expanded


def predictive_view(
    schedule: RecurringTransaction, accounts: Sequence[Mapping[str, Any]]
) -> Any:
    """What a schedule adds to the predictive forecast, which counts available money only (Q6, Q7).

    A plain schedule is returned as it is. A transfer counts only where it crosses the boundary of
    available money: out of an available account into one that is not (a switched-off reserve, a
    card, an investment) it is money leaving, the other way it is money arriving, and between two
    available accounts (or two others) it is nothing. Returns None for nothing.
    """
    if not is_transfer_schedule(schedule):
        return schedule
    by_id = {str(account["id"]): account for account in accounts}
    source = by_id.get(str(schedule.account_id))
    destination = by_id.get(str(schedule.destination_account_id))
    if source is None or destination is None:
        return None
    if source.get("is_closed") or destination.get("is_closed"):
        return None
    source_in, destination_in = counts_as_available(source), counts_as_available(destination)
    if source_in == destination_in:
        return None
    source_account = _AccountStub(source)
    destination_account = _AccountStub(destination)
    debit, credit = legs_of(schedule, source_account, destination_account)
    return debit if source_in else credit


class _AccountStub:
    """The two attributes a leg reads from an account, from a serialized account."""

    def __init__(self, account: Mapping[str, Any]) -> None:
        raw = account["id"]
        self.id = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        self.currency = account["currency"]
