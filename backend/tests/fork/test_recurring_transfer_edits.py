"""F-004 edits after the deep review: a transfer whose account was closed can still be deactivated,
end-dated or renamed; a split expense turned into a transfer keeps no category; amounts are positive;
an ordinary placeholder marked as a transfer still meets its bank row. Synthetic data."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

import mcp_server.tools  # noqa: F401
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import recurring_match_service
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.fork._transfer_data import account, next_month_day, transfer_schedule


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


async def _closed_destination(session, user, ws):
    checking = await account(session, user, ws, "Checking")
    savings = await account(session, user, ws, "Savings", type_="savings")
    rec = await transfer_schedule(session, user, ws, checking, savings)
    savings.is_closed = True
    await session.commit()
    return checking, savings, rec


@pytest.mark.asyncio
async def test_closed_account_transfer_can_still_be_stopped(client, auth_headers, session, test_user, test_workspace):
    checking, savings, rec = await _closed_destination(session, test_user, test_workspace)
    url = f"/api/recurring-transactions/{rec.id}"
    for body in (
        {"description": "Old savings plan"},
        {"amount": "150.00"},
        {"end_date": next_month_day(28).isoformat()},
        {"is_active": False},
        # The form sends the whole schedule back, accounts unchanged.
        {"account_id": str(checking.id), "destination_account_id": str(savings.id), "type": "debit",
         "currency": "BRL", "category_id": None, "is_active": False},
    ):
        response = await client.patch(url, json=body, headers=auth_headers)
        assert response.status_code == 200, (body, response.text)
    other = await account(session, test_user, test_workspace, "Wallet")
    await session.commit()
    moved = await client.patch(url, json={"account_id": str(other.id)}, headers=auth_headers)
    assert moved.status_code == 400 and "closed account" in moved.json()["detail"]


@pytest.mark.asyncio
async def test_closed_account_transfer_cancels_over_mcp(session, test_user, test_workspace, ext_ctx):
    _, _, rec = await _closed_destination(session, test_user, test_workspace)
    result = await REGISTRY["propose_cancel_recurring_transaction"].handler(
        session=session, ctx=ext_ctx, recurring_id=str(rec.id), apply=True
    )
    assert result["applied"] is True and result["is_active"] is False


@pytest.mark.asyncio
async def test_split_expense_turned_into_a_transfer_keeps_no_category(client, auth_headers, session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    home = Category(id=uuid.uuid4(), user_id=test_user.id, name="Home")
    food = Category(id=uuid.uuid4(), user_id=test_user.id, name="Food")
    session.add_all([home, food])
    await session.commit()
    created = await client.post("/api/recurring-transactions", json={
        "description": "Household", "amount": "1000.00", "currency": "BRL", "type": "debit",
        "frequency": "monthly", "day_of_month": 10, "start_date": next_month_day(10).isoformat(),
        "account_id": str(checking.id),
        "category_lines": {"share_type": "percent", "lines": [
            {"category_id": str(home.id), "share_pct": "70"}, {"category_id": str(food.id), "share_pct": "30"},
        ]},
    }, headers=auth_headers)
    assert created.status_code == 201, created.text
    url = f"/api/recurring-transactions/{created.json()['id']}"
    turned = await client.patch(url, json={
        "destination_account_id": str(savings.id), "category_id": None,
        "category_lines": {"share_type": "exact", "lines": []},
    }, headers=auth_headers)
    assert turned.status_code == 200, turned.text
    body = turned.json()
    assert (body["category_id"], body["has_category_lines"]) == (None, False)
    again = await client.patch(url, json={"description": "Household reserve"}, headers=auth_headers)
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_transfer_amount_must_be_positive(client, auth_headers, session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    savings = await account(session, test_user, test_workspace, "Savings", type_="savings")
    rec = await transfer_schedule(session, test_user, test_workspace, checking, savings)
    await session.commit()
    for amount in ("0", "-200.00"):
        response = await client.post("/api/recurring-transactions", json={
            "description": "Nothing", "amount": amount, "currency": "BRL", "type": "debit",
            "frequency": "monthly", "day_of_month": 5, "start_date": next_month_day(5).isoformat(),
            "account_id": str(checking.id), "destination_account_id": str(savings.id),
        }, headers=auth_headers)
        assert response.status_code == 400 and "greater than zero" in response.json()["detail"], response.text
        edited = await client.patch(
            f"/api/recurring-transactions/{rec.id}", json={"amount": amount}, headers=auth_headers
        )
        assert edited.status_code == 400 and "greater than zero" in edited.json()["detail"]


@pytest.mark.asyncio
async def test_ordinary_placeholder_marked_as_transfer_still_meets_its_bank_row(session, test_user, test_workspace):
    checking = await account(session, test_user, test_workspace, "Checking")
    day = next_month_day(10)
    bill = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=checking.id,
        description="Card bill", amount=Decimal("300"), currency="BRL", type="debit", frequency="monthly",
        day_of_month=10, start_date=day, next_occurrence=day + timedelta(days=31),
    )
    session.add(bill)
    await session.flush()
    placeholder = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=checking.id,
        description="Card bill", amount=Decimal("300"), currency="BRL", date=day, effective_date=day,
        type="debit", source="recurring", status="pending", recurring_transaction_id=bill.id,
        transfer_pair_id=uuid.uuid4(),  # the user paired it with the card payment, as upstream allows
    )
    session.add(placeholder)
    await session.commit()
    found = await recurring_match_service.find_placeholder_for_incoming(
        session, checking.id, Decimal("300"), "BRL", "debit", day, "Card bill",
    )
    assert found is not None and found.id == placeholder.id
