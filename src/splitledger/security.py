"""Password hashing (Argon2id) and stateless access tokens (JWT)."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from splitledger.errors import AuthenticationError

_hasher = PasswordHasher()

TOKEN_TYPE = "access"  # noqa: S105


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id using the library defaults."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without ever raising on a malformed stored hash."""
    try:
        _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash used weaker parameters than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(
    subject: uuid.UUID,
    *,
    secret: str,
    algorithm: str,
    ttl_minutes: int,
    now: datetime | None = None,
) -> str:
    """Mint a signed access token for a user id."""
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": TOKEN_TYPE,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str) -> uuid.UUID:
    """Validate a token and return its subject, or raise AuthenticationError."""
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Access token is invalid.") from exc

    if payload.get("typ") != TOKEN_TYPE:
        raise AuthenticationError("Unexpected token type.")

    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise AuthenticationError("Access token is missing a subject.")
    try:
        return uuid.UUID(subject)
    except ValueError as exc:
        raise AuthenticationError("Access token subject is malformed.") from exc
