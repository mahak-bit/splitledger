"""Group lifecycle and membership rules."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from splitledger.errors import ConflictError, NotFoundError
from splitledger.models import Group, GroupMember, MemberRole, User
from splitledger.schemas import GroupCreate, MemberAdd
from splitledger.services import users as user_service
from splitledger.services.balances import compute_balances


async def create_group(session: AsyncSession, owner: User, payload: GroupCreate) -> Group:
    """Create a group and enrol its creator as the first owner."""
    group = Group(
        name=payload.name.strip(),
        description=payload.description,
        currency=payload.currency,
        created_by_id=owner.id,
    )
    group.members.append(GroupMember(user_id=owner.id, role=MemberRole.OWNER))
    session.add(group)
    await session.commit()
    return await get_group(session, group.id)


async def get_group(session: AsyncSession, group_id: uuid.UUID) -> Group:
    # populate_existing refreshes an instance already in the identity map, so a caller
    # that has just added members sees them instead of a stale collection.
    statement = select(Group).where(Group.id == group_id).execution_options(populate_existing=True)
    result = await session.execute(statement)
    group = result.scalar_one_or_none()
    if group is None:
        raise NotFoundError(f"No group with id {group_id}.")
    return group


async def require_member(
    session: AsyncSession,
    group_id: uuid.UUID,
    user: User,
) -> tuple[Group, GroupMember]:
    """Fetch a group only if the caller belongs to it.

    A non-member gets 404 rather than 403: whether a given group exists is not
    something an outsider should be able to probe.
    """
    result = await session.execute(
        select(Group, GroupMember)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(Group.id == group_id, GroupMember.user_id == user.id)
    )
    row = result.first()
    if row is None:
        raise NotFoundError(f"No group with id {group_id}.")
    group, membership = row
    return group, membership


async def list_for_user(
    session: AsyncSession,
    user: User,
    *,
    limit: int,
    offset: int,
) -> tuple[list[Group], int]:
    """Page through the groups the caller belongs to, newest first."""
    base = (
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == user.id)
    )
    total = await session.scalar(
        select(func.count()).select_from(GroupMember).where(GroupMember.user_id == user.id)
    )
    result = await session.execute(
        base.order_by(Group.created_at.desc(), Group.id).limit(limit).offset(offset)
    )
    return list(result.scalars().unique()), int(total or 0)


async def get_membership(
    session: AsyncSession,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
) -> GroupMember:
    result = await session.execute(
        select(GroupMember)
        .options(joinedload(GroupMember.user))
        .where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise NotFoundError("That user is not a member of this group.")
    return membership


async def list_members(session: AsyncSession, group_id: uuid.UUID) -> list[GroupMember]:
    result = await session.execute(
        select(GroupMember)
        .options(joinedload(GroupMember.user))
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.joined_at, GroupMember.user_id)
    )
    return list(result.scalars().unique())


async def add_member(session: AsyncSession, group: Group, payload: MemberAdd) -> GroupMember:
    """Add an existing account to the group."""
    user = await user_service.get_by_email(session, payload.email)
    if user is None:
        raise NotFoundError("No account with that email address; ask them to register first.")

    existing = await session.get(GroupMember, (group.id, user.id))
    if existing is not None:
        raise ConflictError("That user is already a member of this group.")

    session.add(GroupMember(group_id=group.id, user_id=user.id, role=payload.role))
    await session.commit()
    return await get_membership(session, group.id, user.id)


async def remove_member(session: AsyncSession, group: Group, user_id: uuid.UUID) -> None:
    """Remove a member, but never one who is still owed money or still owes it."""
    membership = await session.get(GroupMember, (group.id, user_id))
    if membership is None:
        raise NotFoundError("That user is not a member of this group.")

    balances = await compute_balances(session, group.id)
    outstanding = balances.get(user_id, 0)
    if outstanding != 0:
        raise ConflictError(
            "Settle this member's balance before removing them.",
            details={"net_minor": outstanding},
        )

    if membership.role is MemberRole.OWNER:
        owners = await session.scalar(
            select(func.count())
            .select_from(GroupMember)
            .where(GroupMember.group_id == group.id, GroupMember.role == MemberRole.OWNER)
        )
        if int(owners or 0) <= 1:
            raise ConflictError("A group must always keep at least one owner.")

    await session.delete(membership)
    await session.commit()
