"""F-006 (#8, Q13): the calendar lists each open invoice it counts as a projected item. Synthetic data only."""
from datetime import date

import pytest

from app.services.transaction_calendar_service import get_transaction_calendar
from tests.fork import _budget_data as data
from tests.fork import _projection_data as pdata


def _items(calendar, on):
    return [item for day in calendar.days if day.date == on for item in day.items if item.source == "invoice"]


@pytest.mark.asyncio
async def test_open_invoices_are_listed_on_their_due_date(session, test_user, test_workspace, monkeypatch):
    data.pin_today(monkeypatch)
    b = await pdata.Base(session, test_user, test_workspace).build()
    imported = await b.invoice("400", date(2026, 11, 27))
    imported.external_number = "NF-7"
    local = await b.invoice("150", date(2026, 11, 27))
    local.origin, local.number, local.snapshot = "local", 31, {"number_prefix": "A/"}
    supplier = await b.invoice("60", date(2026, 12, 3), direction="payable")
    supplier.external_number = "SUP-2"
    await session.commit()

    calendar = await get_transaction_calendar(session, test_workspace.id, test_user.id, month=date(2026, 11, 1))
    listed = sorted((i.description, i.type, i.amount, i.invoice_number, i.kind) for i in _items(calendar, date(2026, 11, 27)))
    assert listed == [
        ("Invoice A/31 (expected)", "credit", 150.0, "A/31", "projected"),
        ("Invoice NF-7 (expected)", "credit", 400.0, "NF-7", "projected"),
    ]
    day = next(d for d in calendar.days if d.date == date(2026, 11, 27))
    assert day.projected_income == 550.0 and day.projected_count == 2
    assert {i.invoice_id for i in _items(calendar, date(2026, 11, 27))} == {imported.id, local.id}

    december = await get_transaction_calendar(session, test_workspace.id, test_user.id, month=date(2026, 12, 1))
    [payable] = _items(december, date(2026, 12, 3))
    assert (payable.description, payable.type, payable.amount) == ("Invoice SUP-2 (expected)", "debit", 60.0)


@pytest.mark.asyncio
async def test_an_account_filter_lists_no_invoice(session, test_user, test_workspace, monkeypatch):
    data.pin_today(monkeypatch)
    b = await pdata.Base(session, test_user, test_workspace).build()
    await b.invoice("400", date(2026, 11, 27))
    calendar = await get_transaction_calendar(
        session, test_workspace.id, test_user.id, month=date(2026, 11, 1), account_ids=[b.checking.id],
    )
    assert _items(calendar, date(2026, 11, 27)) == []
