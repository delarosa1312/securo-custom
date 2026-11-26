"""F-001 MCP tools: the field in list_accounts, get_available_balance, and the
propose_set_account_available preview and apply. Synthetic accounts only."""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401
from app.models.account import Account
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from mcp_server.auth import CallContext
from mcp_server.protocol import tool_annotations
from mcp_server.registry import REGISTRY


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


async def _account(session, user, ws, name, type_, balance, *, closed=False, include=True):
    from app.models.transaction import Transaction
    from app.core.app_clock import app_today

    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, name=name, type=type_,
        balance=Decimal(balance), currency="BRL", is_closed=closed, include_in_available=include,
    )
    session.add(account)
    await session.flush()
    if Decimal(balance):
        session.add(
            Transaction(
                id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, account_id=account.id,
                description="Saldo inicial", amount=abs(Decimal(balance)), currency="BRL",
                date=app_today(), type="credit" if type_ not in {"credit_card", "loan"} else "debit",
                source="opening_balance", status="posted",
            )
        )
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def accounts(session, test_user, test_workspace) -> dict[str, Account]:
    return {
        "checking": await _account(session, test_user, test_workspace, "Current account", "checking", "500.00"),
        "wallet": await _account(session, test_user, test_workspace, "Cash", "wallet", "100.00"),
        "savings": await _account(session, test_user, test_workspace, "Reserve", "savings", "1000.00"),
        "card": await _account(session, test_user, test_workspace, "Card", "credit_card", "300.00"),
    }


def _call(name):
    return REGISTRY[name].handler


def test_tools_are_registered_with_the_right_hints():
    assert REGISTRY["get_available_balance"].is_proposal is False
    assert REGISTRY["propose_set_account_available"].is_proposal is True
    hints = tool_annotations("propose_set_account_available", is_proposal=True)
    assert hints["readOnlyHint"] is False


@pytest.mark.asyncio
async def test_list_accounts_shows_the_setting(session, ctx, accounts):
    accounts["savings"].include_in_available = False
    await session.commit()
    result = await _call("list_accounts")(session=session, ctx=ctx)
    by_name = {item["name"]: item for item in result["items"]}
    assert by_name["Reserve"]["include_in_available"] is False
    assert by_name["Current account"]["include_in_available"] is True


@pytest.mark.asyncio
async def test_get_available_balance(session, ctx, accounts):
    accounts["savings"].include_in_available = False
    await session.commit()
    result = await _call("get_available_balance")(session=session, ctx=ctx)
    assert result["primary_currency"] == "BRL"
    assert result["available_balance"] == 600.0
    assert result["by_currency"] == {"BRL": 600.0}
    assert {a["name"] for a in result["accounts"]} == {"Current account", "Cash"}
    assert [a["name"] for a in result["excluded_accounts"]] == ["Reserve"]

    scoped = await _call("get_available_balance")(
        session=session, ctx=ctx, account_ids=[str(accounts["checking"].id), str(accounts["savings"].id)]
    )
    assert scoped["available_balance"] == 500.0
    empty = await _call("get_available_balance")(session=session, ctx=ctx, account_ids=[])
    assert empty["available_balance"] == 0.0 and empty["accounts"] == []


@pytest.mark.asyncio
async def test_preview_writes_nothing(session, ctx, accounts):
    preview = await _call("propose_set_account_available")(
        session=session, ctx=ctx, account_id=str(accounts["savings"].id), include_in_available=False,
    )
    assert preview["kind"] == "set_account_available"
    assert preview["target"]["name"] == "Reserve"
    assert preview["target"]["include_in_available"] is True
    assert preview["changes"] == {"include_in_available": False}
    assert preview["apply_endpoint"] == f"PATCH /api/accounts/{accounts['savings'].id}"
    assert "applied" not in preview
    await session.refresh(accounts["savings"])
    assert accounts["savings"].include_in_available is True


@pytest.mark.asyncio
async def test_internal_caller_cannot_apply(session, ctx, accounts):
    result = await _call("propose_set_account_available")(
        session=session, ctx=ctx, account_id=str(accounts["savings"].id),
        include_in_available=False, apply=True,
    )
    assert "applied" not in result
    await session.refresh(accounts["savings"])
    assert accounts["savings"].include_in_available is True


@pytest.mark.asyncio
async def test_external_apply_writes(session, ext_ctx, accounts):
    result = await _call("propose_set_account_available")(
        session=session, ctx=ext_ctx, account_id=str(accounts["savings"].id),
        include_in_available=False, apply=True,
    )
    assert result["applied"] is True
    await session.refresh(accounts["savings"])
    assert accounts["savings"].include_in_available is False
    after = await _call("get_available_balance")(session=session, ctx=ext_ctx)
    assert after["available_balance"] == 600.0


@pytest.mark.asyncio
async def test_external_viewer_cannot_apply(session, test_user, accounts):
    viewer_ctx = CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True, role="viewer")
    result = await _call("propose_set_account_available")(
        session=session, ctx=viewer_ctx, account_id=str(accounts["savings"].id),
        include_in_available=False, apply=True,
    )
    assert "applied" not in result
    await session.refresh(accounts["savings"])
    assert accounts["savings"].include_in_available is True


@pytest.mark.asyncio
async def test_refusals(session, ctx, test_user, test_workspace, accounts):
    propose = _call("propose_set_account_available")
    card = await propose(session=session, ctx=ctx, account_id=str(accounts["card"].id), include_in_available=False)
    assert "never part of the available balance" in card["error"]
    closed = await _account(session, test_user, test_workspace, "Old", "checking", "0", closed=True)
    result = await propose(session=session, ctx=ctx, account_id=str(closed.id), include_in_available=False)
    assert "closed" in result["error"]
    same = await propose(session=session, ctx=ctx, account_id=str(accounts["checking"].id), include_in_available=True)
    assert same["error"] == "nothing to change"
    bad = await propose(session=session, ctx=ctx, account_id="not-a-uuid", include_in_available=False)
    assert bad["error"] == "account not found"


@pytest.mark.asyncio
async def test_other_workspace_account_is_not_found(session: AsyncSession, ext_ctx, accounts):
    stranger = User(
        id=uuid.uuid4(), email="stranger-mcp@example.com", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(stranger)
    await session.flush()
    ws = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=stranger.id,
                   default_currency="BRL", locale="pt-BR")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=stranger.id, role="owner"))
    foreign = await _account(session, stranger, ws, "Foreign", "savings", "10.00")
    result = await _call("propose_set_account_available")(
        session=session, ctx=ext_ctx, account_id=str(foreign.id), include_in_available=False, apply=True,
    )
    assert result == {"error": "account not found"}
    await session.refresh(foreign)
    assert foreign.include_in_available is True
    read = await _call("get_available_balance")(session=session, ctx=ext_ctx, account_ids=[str(foreign.id)])
    assert read["available_balance"] == 0.0 and read["accounts"] == []
