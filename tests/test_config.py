"""Settings guard-rails -- above all, the ones that block an unsafe production boot."""

import os

import pytest
from pydantic import ValidationError

from splitledger.config import INSECURE_DEFAULT_SECRET, MIN_SECRET_LENGTH, Settings


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read the process environment; blank it so these tests are hermetic."""
    for name in list(os.environ):
        if name.startswith("SPLITLEDGER_"):
            monkeypatch.delenv(name, raising=False)


def test_defaults_are_usable_in_development() -> None:
    settings = Settings()
    assert settings.environment == "development"
    assert settings.jwt_secret == INSECURE_DEFAULT_SECRET
    assert settings.database_url.startswith("sqlite+aiosqlite")


def test_production_refuses_the_shipped_default_secret() -> None:
    with pytest.raises(ValidationError, match="SPLITLEDGER_JWT_SECRET"):
        Settings(environment="production", jwt_secret=INSECURE_DEFAULT_SECRET)


def test_production_refuses_a_short_secret() -> None:
    with pytest.raises(ValidationError, match=f"at least {MIN_SECRET_LENGTH} characters"):
        Settings(environment="production", jwt_secret="too-short")


def test_production_accepts_a_long_unique_secret() -> None:
    secret = "s" * MIN_SECRET_LENGTH
    assert Settings(environment="production", jwt_secret=secret).jwt_secret == secret


def test_token_lifetime_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(access_token_ttl_minutes=0)


def test_currency_must_be_three_characters() -> None:
    with pytest.raises(ValidationError):
        Settings(default_currency="DOLLAR")
