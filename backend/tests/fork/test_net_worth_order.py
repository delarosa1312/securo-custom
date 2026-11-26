"""F-006 (#8): the net-worth report lists accounts in the Accounts page order. Synthetic data only."""
from datetime import date

import pytest

from app.services.report_service import get_net_worth_report
from tests.fork import _budget_data as data


@pytest.mark.asyncio
async def test_accounts_keep_name_order_after_an_edit(session, test_user, test_workspace, monkeypatch):
    data.pin_today(monkeypatch)
    zeta = await data.account(session, test_user, test_workspace, "Zeta")
    alpha = await data.account(session, test_user, test_workspace, "alpha")
    for acct in (zeta, alpha):
        await data.txn(session, test_user, test_workspace, acct, "100", date(2026, 10, 1), "credit",
                       source="opening_balance")
    zeta.display_name = "Edited"
    await session.commit()

    report = await get_net_worth_report(session, test_workspace.id, test_user.id, months=1)
    labels = [item.label for item in report.composition]
    assert labels.index("alpha") < labels.index("Edited")
