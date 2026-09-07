"""Application settings, loaded from the environment (12-factor style)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

INSECURE_DEFAULT_SECRET = "dev-insecure-secret-do-not-use-in-production"  # noqa: S105
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Runtime configuration. Every field maps to a SPLITLEDGER_* environment variable."""

    model_config = SettingsConfigDict(
        env_prefix="SPLITLEDGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SplitLedger"
    environment: Environment = "development"
    debug: bool = False

    database_url: str = "sqlite+aiosqlite:///./splitledger.db"
    database_echo: bool = False

    jwt_secret: str = INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = Field(default=720, ge=1)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    default_currency: str = Field(default="USD", min_length=3, max_length=3)

    # Finding the provably minimal set of transfers is exponential in group size.
    # Above this many members with a non-zero balance we fall back to a greedy match.
    settlement_exact_threshold: int = Field(default=12, ge=2, le=16)

    @model_validator(mode="after")
    def _reject_insecure_production(self) -> "Settings":
        """Refuse to boot a production process with a guessable signing key."""
        if self.environment != "production":
            return self
        if self.jwt_secret == INSECURE_DEFAULT_SECRET:
            msg = (
                "SPLITLEDGER_JWT_SECRET must be overridden with a unique value "
                "when SPLITLEDGER_ENVIRONMENT=production"
            )
            raise ValueError(msg)
        if len(self.jwt_secret) < MIN_SECRET_LENGTH:
            msg = (
                f"SPLITLEDGER_JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters; "
                "RFC 7518 recommends a key at least as long as the HMAC output"
            )
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached; cleared in tests)."""
    return Settings()
