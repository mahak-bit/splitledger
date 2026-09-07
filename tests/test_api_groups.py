"""Group creation, membership rules, and the access-control boundary."""

import uuid

from httpx import AsyncClient

from tests.conftest import Account, AccountFactory


async def create_group(client: AsyncClient, owner: Account, name: str = "Lisbon") -> uuid.UUID:
    response = await client.post(
        "/api/v1/groups",
        json={"name": name, "currency": "EUR"},
        headers=owner.headers,
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def test_creator_becomes_the_owner(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    response = await client.post(
        "/api/v1/groups",
        json={"name": "Lisbon", "description": "Spring trip", "currency": "EUR"},
        headers=ada.headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["currency"] == "EUR"
    assert len(body["members"]) == 1
    assert body["members"][0]["role"] == "owner"
    assert body["members"][0]["user"]["email"] == ada.email


async def test_currency_must_be_an_iso_code(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    response = await client.post(
        "/api/v1/groups", json={"name": "Lisbon", "currency": "euro"}, headers=ada.headers
    )
    assert response.status_code == 422


async def test_groups_are_listed_only_for_their_members(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    await create_group(client, ada)

    mine = await client.get("/api/v1/groups", headers=ada.headers)
    theirs = await client.get("/api/v1/groups", headers=blaise.headers)

    assert mine.json()["total"] == 1
    assert theirs.json()["total"] == 0


async def test_outsiders_cannot_tell_a_group_exists(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await create_group(client, ada)

    response = await client.get(f"/api/v1/groups/{group_id}", headers=blaise.headers)
    # 404 rather than 403: existence itself is not public information.
    assert response.status_code == 404


async def test_owner_adds_a_member(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await create_group(client, ada)

    added = await client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"email": blaise.email},
        headers=ada.headers,
    )
    assert added.status_code == 201
    assert added.json()["role"] == "member"

    detail = await client.get(f"/api/v1/groups/{group_id}", headers=blaise.headers)
    assert detail.status_code == 200
    assert len(detail.json()["members"]) == 2


async def test_plain_members_cannot_invite(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    cleo = await accounts.create("Cleo")
    group_id = await create_group(client, ada)
    await client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"email": blaise.email},
        headers=ada.headers,
    )

    response = await client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"email": cleo.email},
        headers=blaise.headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_inviting_an_unregistered_address_is_a_404(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    group_id = await create_group(client, ada)

    response = await client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"email": "ghost@example.com"},
        headers=ada.headers,
    )
    assert response.status_code == 404


async def test_adding_the_same_member_twice_conflicts(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await create_group(client, ada)
    body = {"email": blaise.email}

    assert (
        await client.post(f"/api/v1/groups/{group_id}/members", json=body, headers=ada.headers)
    ).status_code == 201
    second = await client.post(f"/api/v1/groups/{group_id}/members", json=body, headers=ada.headers)
    assert second.status_code == 409


async def test_a_member_who_still_owes_money_cannot_be_removed(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    ada = await accounts.create("Ada")
    blaise = await accounts.create("Blaise")
    group_id = await create_group(client, ada)
    await client.post(
        f"/api/v1/groups/{group_id}/members",
        json={"email": blaise.email},
        headers=ada.headers,
    )
    await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "description": "Taxi",
            "amount": "40.00",
            "paid_by": str(ada.id),
            "strategy": "equal",
            "participants": [str(ada.id), str(blaise.id)],
        },
        headers=ada.headers,
    )

    blocked = await client.delete(
        f"/api/v1/groups/{group_id}/members/{blaise.id}", headers=ada.headers
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["net_minor"] == -2000

    await client.post(
        f"/api/v1/groups/{group_id}/settlements",
        json={"to_user": str(ada.id), "amount": "20.00"},
        headers=blaise.headers,
    )
    allowed = await client.delete(
        f"/api/v1/groups/{group_id}/members/{blaise.id}", headers=ada.headers
    )
    assert allowed.status_code == 204


async def test_the_last_owner_cannot_leave(client: AsyncClient, accounts: AccountFactory) -> None:
    ada = await accounts.create("Ada")
    group_id = await create_group(client, ada)

    response = await client.delete(
        f"/api/v1/groups/{group_id}/members/{ada.id}", headers=ada.headers
    )
    assert response.status_code == 409
    assert "owner" in response.json()["error"]["message"]
