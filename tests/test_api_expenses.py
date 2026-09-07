"""Expense recording: split strategies, validation, and delete permissions."""

import uuid
from decimal import Decimal
from typing import Any

from httpx import AsyncClient

from tests.conftest import Account, AccountFactory


def money(value: Any) -> Decimal:
    """Read a money field without caring whether it arrived as a string or a number."""
    return Decimal(str(value))


async def build_group(client: AsyncClient, owner: Account, *members: Account) -> uuid.UUID:
    created = await client.post(
        "/api/v1/groups", json={"name": "Trip", "currency": "USD"}, headers=owner.headers
    )
    assert created.status_code == 201, created.text
    group_id = uuid.UUID(created.json()["id"])
    for member in members:
        added = await client.post(
            f"/api/v1/groups/{group_id}/members",
            json={"email": member.email},
            headers=owner.headers,
        )
        assert added.status_code == 201, added.text
    return group_id


async def test_equal_split_distributes_the_odd_cent(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    cleo = await accounts.create("Cleo")
    group_id = await build_group(client, ada, blaise, cleo)

    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Dinner",
            "amount": "10.00",
            "paid_by": str(ada.id),
            "strategy": "equal",
            "participants": [str(ada.id), str(blaise.id), str(cleo.id)],
        },
        headers=ada.headers,
    )

    assert response.status_code == 201, response.text
    shares = sorted(money(share["amount"]) for share in response.json()["shares"])
    assert shares == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]
    assert sum(shares) == Decimal("10.00")


async def test_share_weights_split_proportionally(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await build_group(client, ada, blaise)

    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Rental car",
            "amount": "240.00",
            "paid_by": str(ada.id),
            "strategy": "shares",
            "shares": [
                {"user_id": str(ada.id), "value": "2"},
                {"user_id": str(blaise.id), "value": "1"},
            ],
        },
        headers=ada.headers,
    )

    assert response.status_code == 201, response.text
    by_user = {share["user_id"]: money(share["amount"]) for share in response.json()["shares"]}
    assert by_user[str(ada.id)] == Decimal("160.00")
    assert by_user[str(blaise.id)] == Decimal("80.00")


async def test_percentages_must_total_one_hundred(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await build_group(client, ada, blaise)

    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Hotel",
            "amount": "100.00",
            "paid_by": str(ada.id),
            "strategy": "percentage",
            "shares": [
                {"user_id": str(ada.id), "value": "60"},
                {"user_id": str(blaise.id), "value": "30"},
            ],
        },
        headers=ada.headers,
    )
    assert response.status_code == 422


async def test_exact_amounts_must_add_up(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await build_group(client, ada, blaise)

    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Tasting menu",
            "amount": "100.00",
            "paid_by": str(ada.id),
            "strategy": "exact",
            "shares": [
                {"user_id": str(ada.id), "value": "60.00"},
                {"user_id": str(blaise.id), "value": "30.00"},
            ],
        },
        headers=ada.headers,
    )
    assert response.status_code == 422


async def test_outsiders_cannot_be_dragged_into_a_split(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    stranger = await accounts.create("Stranger")
    group_id = await build_group(client, ada)

    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Dinner",
            "amount": "10.00",
            "paid_by": str(ada.id),
            "strategy": "equal",
            "participants": [str(ada.id), str(stranger.id)],
        },
        headers=ada.headers,
    )
    assert response.status_code == 422
    assert str(stranger.id) in response.json()["error"]["details"]["non_members"]


async def test_the_payer_must_be_a_member(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    stranger = await accounts.create("Stranger")
    group_id = await build_group(client, ada)

    response = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Dinner",
            "amount": "10.00",
            "paid_by": str(stranger.id),
            "strategy": "equal",
            "participants": [str(ada.id)],
        },
        headers=ada.headers,
    )
    assert response.status_code == 422


async def test_expenses_are_listed_and_filterable(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await build_group(client, ada, blaise)
    for payer in (ada, ada, blaise):
        await client.post(
            f"/api/v1/groups/{group_id}/expenses",
            json={
                "description": "Coffee",
                "amount": "6.00",
                "paid_by": str(payer.id),
                "strategy": "equal",
                "participants": [str(ada.id), str(blaise.id)],
            },
            headers=payer.headers,
        )

    everything = await client.get(f"/api/v1/groups/{group_id}/expenses", headers=ada.headers)
    filtered = await client.get(
        f"/api/v1/groups/{group_id}/expenses",
        params={"paid_by": str(blaise.id)},
        headers=ada.headers,
    )

    assert everything.json()["total"] == 3
    assert filtered.json()["total"] == 1


async def test_only_the_author_or_an_owner_can_delete(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    cleo = await accounts.create("Cleo")
    group_id = await build_group(client, ada, blaise, cleo)

    created = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Snacks",
            "amount": "9.00",
            "paid_by": str(blaise.id),
            "strategy": "equal",
            "participants": [str(ada.id), str(blaise.id), str(cleo.id)],
        },
        headers=blaise.headers,
    )
    expense_id = created.json()["id"]

    refused = await client.delete(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}", headers=cleo.headers
    )
    assert refused.status_code == 403

    allowed = await client.delete(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}", headers=ada.headers
    )
    assert allowed.status_code == 204

    gone = await client.get(f"/api/v1/groups/{group_id}/expenses/{expense_id}", headers=ada.headers)
    assert gone.status_code == 404
