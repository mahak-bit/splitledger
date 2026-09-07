"""End-to-end: expenses in, balances out, settlement plan, then settle up."""

import uuid
from decimal import Decimal

from httpx import AsyncClient

from tests.conftest import Account, AccountFactory
from tests.test_api_expenses import build_group, money


async def add_expense(
    client: AsyncClient,
    group_id: uuid.UUID,
    payer: Account,
    amount: str,
    participants: list[Account],
) -> None:
    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Shared cost",
            "amount": amount,
            "paid_by": str(payer.id),
            "strategy": "equal",
            "participants": [str(person.id) for person in participants],
        },
        headers=payer.headers,
    )
    assert response.status_code == 201, response.text


async def test_balances_reflect_who_paid_for_what(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    cleo = await accounts.create("Cleo")
    group_id = await build_group(client, ada, blaise, cleo)
    everyone = [ada, blaise, cleo]

    await add_expense(client, group_id, ada, "90.00", everyone)
    await add_expense(client, group_id, blaise, "30.00", everyone)

    response = await client.get(f"/api/v1/groups/{group_id}/balances", headers=ada.headers)
    assert response.status_code == 200
    balances = {entry["user_id"]: money(entry["net"]) for entry in response.json()["balances"]}

    # Ada paid 90 and owes 40; Blaise paid 30 and owes 40; Cleo paid nothing.
    assert balances[str(ada.id)] == Decimal("50.00")
    assert balances[str(blaise.id)] == Decimal("-10.00")
    assert balances[str(cleo.id)] == Decimal("-40.00")
    assert sum(balances.values()) == Decimal("0.00")


async def test_settlement_plan_clears_the_ledger_in_minimal_payments(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    cleo = await accounts.create("Cleo")
    group_id = await build_group(client, ada, blaise, cleo)
    everyone = [ada, blaise, cleo]

    await add_expense(client, group_id, ada, "90.00", everyone)
    await add_expense(client, group_id, blaise, "30.00", everyone)

    plan = await client.get(f"/api/v1/groups/{group_id}/settlement-plan", headers=cleo.headers)
    assert plan.status_code == 200
    body = plan.json()

    assert body["method"] == "exact"
    # Three people, one zero-sum component: two payments is the floor.
    assert body["transfer_count"] == 2
    assert money(body["total_amount"]) == Decimal("50.00")

    for transfer in body["transfers"]:
        assert transfer["payee"] == str(ada.id)
        assert money(transfer["amount"]) > 0


async def test_recording_the_plan_zeroes_every_balance(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    cleo = await accounts.create("Cleo")
    group_id = await build_group(client, ada, blaise, cleo)
    everyone = [ada, blaise, cleo]
    by_id = {str(person.id): person for person in everyone}

    await add_expense(client, group_id, ada, "90.00", everyone)
    await add_expense(client, group_id, blaise, "30.00", everyone)

    plan = await client.get(f"/api/v1/groups/{group_id}/settlement-plan", headers=ada.headers)
    for transfer in plan.json()["transfers"]:
        payer = by_id[transfer["payer"]]
        recorded = await client.post(
            f"/api/v1/groups/{group_id}/settlements",
            json={
                "to_user": transfer["payee"],
                "amount": str(transfer["amount"]),
                "note": "settling up",
            },
            headers=payer.headers,
        )
        assert recorded.status_code == 201, recorded.text

    balances = await client.get(f"/api/v1/groups/{group_id}/balances", headers=ada.headers)
    assert all(money(entry["net"]) == 0 for entry in balances.json()["balances"])

    empty_plan = await client.get(f"/api/v1/groups/{group_id}/settlement-plan", headers=ada.headers)
    assert empty_plan.json()["transfer_count"] == 0


async def test_a_settlement_needs_two_different_members(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    group_id = await build_group(client, ada)

    response = await client.post(
        f"/api/v1/groups/{group_id}/settlements",
        json={"to_user": str(ada.id), "amount": "5.00"},
        headers=ada.headers,
    )
    assert response.status_code == 422


async def test_settlements_are_listed_for_the_group(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await build_group(client, ada, blaise)

    await client.post(
        f"/api/v1/groups/{group_id}/settlements",
        json={"to_user": str(ada.id), "amount": "12.50"},
        headers=blaise.headers,
    )

    listed = await client.get(f"/api/v1/groups/{group_id}/settlements", headers=ada.headers)
    assert listed.json()["total"] == 1
    entry = listed.json()["items"][0]
    assert entry["from_user"] == str(blaise.id)
    assert money(entry["amount"]) == Decimal("12.50")


async def test_ledger_endpoints_are_closed_to_outsiders(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    stranger = await accounts.create("Stranger")
    group_id = await build_group(client, ada)

    for path in ("balances", "settlement-plan", "expenses", "settlements"):
        response = await client.get(f"/api/v1/groups/{group_id}/{path}", headers=stranger.headers)
        assert response.status_code == 404, path
