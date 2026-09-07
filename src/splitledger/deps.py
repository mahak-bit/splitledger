"""FastAPI dependencies: authentication, and group-scoped access control."""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from splitledger.config import Settings, get_settings
from splitledger.db import get_session
from splitledger.errors import AuthenticationError, PermissionDeniedError
from splitledger.models import Group, GroupMember, MemberRole, User
from splitledger.security import decode_access_token
from splitledger.services import groups as group_service

# auto_error is off so a missing header raises our own error envelope, not
# Starlette's bare {"detail": ...}.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    session: DbSession,
    settings: AppSettings,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """Resolve the bearer token to an active user."""
    if not token:
        raise AuthenticationError("Missing bearer token.")

    user_id = decode_access_token(
        token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Token does not identify an active user.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(frozen=True)
class GroupContext:
    """Everything a group-scoped handler needs, resolved once by the dependency."""

    group: Group
    membership: GroupMember
    user: User


async def get_group_context(
    group_id: Annotated[uuid.UUID, Path(description="Group identifier.")],
    session: DbSession,
    user: CurrentUser,
) -> GroupContext:
    group, membership = await group_service.require_member(session, group_id, user)
    return GroupContext(group=group, membership=membership, user=user)


GroupAccess = Annotated[GroupContext, Depends(get_group_context)]


async def get_owner_context(context: GroupAccess) -> GroupContext:
    """Same as GroupAccess, but rejects members who do not own the group."""
    if context.membership.role is not MemberRole.OWNER:
        raise PermissionDeniedError("Only a group owner can perform this action.")
    return context


OwnerAccess = Annotated[GroupContext, Depends(get_owner_context)]


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def get_pagination(
    limit: Annotated[int, Query(ge=1, le=200, description="Page size.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PageParams = Annotated[Pagination, Depends(get_pagination)]
