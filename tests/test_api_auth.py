"""Registration, login and token handling."""

from httpx import AsyncClient

from tests.conftest import TEST_PASSWORD, AccountFactory


async def test_health_endpoint_is_public(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["X-Request-ID"]
    assert float(response.headers["X-Response-Time-ms"]) >= 0


async def test_register_returns_the_profile_without_the_password(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "Ada@Example.com", "display_name": "Ada", "password": TEST_PASSWORD},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"  # stored case-folded
    assert "password" not in body
    assert "password_hash" not in body


async def test_duplicate_registration_conflicts(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "display_name": "Dup", "password": TEST_PASSWORD}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_short_passwords_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "display_name": "Short", "password": "abc"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_login_with_a_bad_password_is_unauthenticated(
    client: AsyncClient, accounts: AccountFactory
) -> None:
    account = await accounts.create("Ada")
    response = await client.post(
        "/api/v1/auth/token",
        data={"username": account.email, "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_login_for_an_unknown_account_looks_identical(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/token",
        data={"username": "nobody@example.com", "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Incorrect email or password."


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_me_rejects_a_forged_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401


async def test_me_returns_the_token_holder(client: AsyncClient, accounts: AccountFactory) -> None:
    account = await accounts.create("Ada")
    response = await client.get("/api/v1/auth/me", headers=account.headers)
    assert response.status_code == 200
    assert response.json()["email"] == account.email
