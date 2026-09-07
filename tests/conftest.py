"""Shared fixtures: an in-memory database, an HTTP client, and account helpers."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from splitledger import models  # noqa: F401 - registers the tables on Base
from splitledger.config import Settings, get_settings
from splitledger.db import Base, get_session
from splitledger.main import create_app

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def settings() -> Settings:
    """Settings the app under test runs with. Never touches the real .env values."""
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-only-secret-value-long-enough-for-hmac-sha256",
        access_token_ttl_minutes=15,
        cors_origins=[],
        settlement_exact_threshold=12,
    )


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """One in-memory SQLite database per test, shared across connections."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(settings: Settings, engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """An ASGI client wired to the test database, with no network or lifespan."""
    app = create_app(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@dataclass(frozen=True)
class Account:
    """A registered user plus the header needed to act as them."""

    id: uuid.UUID
    email: str
    display_name: str
    headers: dict[str, str]


class AccountFactory:
    """Registers users and logs them in, so tests can focus on the behaviour."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def create(self, name: str) -> Account:
        email = f"{name.lower()}.{uuid.uuid4().hex[:8]}@example.com"
        registered = await self._client.post(
            "/api/v1/auth/register",
            json={"email": email, "display_name": name, "password": TEST_PASSWORD},
        )
        assert registered.status_code == 201, registered.text

        token = await self._client.post(
            "/api/v1/auth/token",
            data={"username": email, "password": TEST_PASSWORD},
        )
        assert token.status_code == 200, token.text

        return Account(
            id=uuid.UUID(registered.json()["id"]),
            email=email,
            display_name=name,
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        )


@pytest.fixture
def accounts(client: AsyncClient) -> AccountFactory:
    return AccountFactory(client)
