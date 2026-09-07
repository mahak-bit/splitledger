"""Registration and credential checking."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from splitledger.errors import AuthenticationError, ConflictError, NotFoundError
from splitledger.models import User
from splitledger.schemas import UserCreate
from splitledger.security import hash_password, needs_rehash, verify_password


def normalise_email(email: str) -> str:
    """Emails are matched case-insensitively, so store one canonical form."""
    return email.strip().lower()


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalise_email(email)))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"No user with id {user_id}.")
    return user


async def register(session: AsyncSession, payload: UserCreate) -> User:
    """Create an account, rejecting an email that is already taken."""
    email = normalise_email(payload.email)
    if await get_by_email(session, email) is not None:
        raise ConflictError("That email address is already registered.", details={"email": email})

    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    """Return the user behind valid credentials, or raise AuthenticationError."""
    user = await get_by_email(session, email)
    if user is None:
        # Burn a hash anyway so an unknown address is not detectable by timing.
        hash_password(password)
        raise AuthenticationError("Incorrect email or password.")

    if not verify_password(password, user.password_hash):
        raise AuthenticationError("Incorrect email or password.")
    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")

    if needs_rehash(user.password_hash):
        # Transparently upgrade hashes when the Argon2 parameters move on.
        user.password_hash = hash_password(password)
        await session.commit()
    return user
