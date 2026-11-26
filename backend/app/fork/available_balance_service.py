"""Loads accounts the way `GET /api/accounts` serializes them and computes the
available balance from them (F-001), so the API and MCP report the dashboard's
own figure."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.accounts import _convert_to_primary
from app.fork.available_balance import AvailableBalance, compute_available_balance
from app.services import account_service


async def get_available_balance(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> AvailableBalance:
    """Available balance of the workspace's open accounts.

    `account_ids` narrows it to a collection's accounts; ids outside the
    workspace match nothing. None means every account.
    """
    accounts = await account_service.get_accounts(session, workspace_id)
    if account_ids is not None:
        wanted = set(account_ids)
        accounts = [acc for acc in accounts if acc["id"] in wanted]
    for acc in accounts:
        await _convert_to_primary(session, acc, primary_currency)
    return compute_available_balance(accounts, primary_currency)
