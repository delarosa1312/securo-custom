"""F-005: a schedule split across categories, changed from a date on. Synthetic data; dates relative to today."""

from decimal import Decimal

import pytest

from tests.fork._lifecycle_data import month_day

pytestmark = pytest.mark.asyncio


async def _account(client, headers) -> dict:
    resp = await client.post(
        "/api/accounts", headers=headers,
        json={"name": "Checking", "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _rent(client, headers, account_id, lines) -> dict:
    start = month_day(1, 5)
    resp = await client.post(
        "/api/recurring-transactions", headers=headers,
        json={
            "description": "Rent + condo", "amount": 1000, "currency": "USD", "type": "debit",
            "frequency": "monthly", "day_of_month": 5, "start_date": start.isoformat(),
            "account_id": account_id, "category_lines": lines,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _parts(body) -> list[tuple[str, Decimal]]:
    return [(line["category_id"], Decimal(str(line["amount"]))) for line in body["category_lines"]]


async def _change(client, headers, rec_id, since, **changes) -> dict:
    resp = await client.post(
        f"/api/recurring-transactions/{rec_id}/change-from", headers=headers,
        json={"from_date": since.isoformat(), "changes": changes, "preview": False},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["schedule"]


async def _get(client, headers, path) -> dict:
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _schedule(client, headers, rec_id) -> dict:
    listed = await _get(client, headers, "/api/recurring-transactions?status=all")
    return next(item for item in listed if item["id"] == rec_id)


async def test_a_percent_split_carries_to_the_new_part_and_its_rows(client, auth_headers, test_categories):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    lines = {"share_type": "percent", "lines": [
        {"category_id": str(home.id), "share_pct": 70}, {"category_id": str(food.id), "share_pct": 30},
    ]}
    rent = await _rent(client, auth_headers, account["id"], lines)
    ahead = await client.post(
        f"/api/recurring-transactions/{rent['id']}/occurrences/{month_day(3, 5)}/record",
        headers=auth_headers, json={},
    )
    assert ahead.status_code == 201, ahead.text
    row_id = ahead.json()["transaction_id"]

    new = await _change(client, auth_headers, rent["id"], month_day(2, 5), amount="1200.00")

    assert new["has_category_lines"] is True and new["category_id"] is None
    assert _parts(new) == [(str(home.id), Decimal("840.00")), (str(food.id), Decimal("360.00"))]
    old = await _schedule(client, auth_headers, rent["id"])
    assert _parts(old) == [(str(home.id), Decimal("700.00")), (str(food.id), Decimal("300.00"))]
    row = await _get(client, auth_headers, f"/api/transactions/{row_id}")
    assert row["recurring_transaction_id"] == new["id"]
    assert Decimal(str(row["amount"])) == Decimal("1200.00")
    assert _parts(row) == [(str(home.id), Decimal("840.00")), (str(food.id), Decimal("360.00"))]


async def test_an_exact_split_keeps_its_proportions_and_new_lines_replace_it(client, auth_headers, test_categories):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    lines = {"share_type": "exact", "lines": [
        {"category_id": str(home.id), "amount": "750.00"}, {"category_id": str(food.id), "amount": "250.00"},
    ]}
    rent = await _rent(client, auth_headers, account["id"], lines)

    resized = await _change(client, auth_headers, rent["id"], month_day(2, 5), amount="1100.00")
    assert _parts(resized) == [(str(home.id), Decimal("825.00")), (str(food.id), Decimal("275.00"))]

    replaced = await _change(
        client, auth_headers, resized["id"], month_day(4, 5),
        category_lines={"share_type": "percent", "lines": [
            {"category_id": str(home.id), "share_pct": 50}, {"category_id": str(food.id), "share_pct": 50},
        ]},
    )
    assert replaced["predecessor_id"] == resized["id"]
    assert _parts(replaced) == [(str(home.id), Decimal("550.00")), (str(food.id), Decimal("550.00"))]
    assert _parts(await _schedule(client, auth_headers, resized["id"])) == [
        (str(home.id), Decimal("825.00")), (str(food.id), Decimal("275.00")),
    ]
