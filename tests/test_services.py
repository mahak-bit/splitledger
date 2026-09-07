"""Service-level tests for behaviour the HTTP layer hides -- mostly session semantics."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from splitledger.errors import AuthenticationError, ConflictError, NotFoundError
from splitledger.models import MemberRole, User
from splitledger.schemas import GroupCreate, MemberAdd, UserCreate
from splitledger.services import groups as group_service
from splitledger.services import users as user_service
from tests.conftest import TEST_PASSWORD


async def make_user(session: AsyncSession, name: str) -> User:
    return await user_service.register(
        session,
        UserCreate(email=f"{name.lower()}@example.com", display_name=name, password=TEST_PASSWORD),
    )


async def test_emails_are_stored_case_folded(session: AsyncSession) -> None:
    user = await user_service.register(
        session,
        UserCreate(email="Ada@Example.COM", display_name="Ada", password=TEST_PASSWORD),
    )
    assert user.email == "ada@example.com"
    assert await user_service.get_by_email(session, "  ADA@example.com  ") is not None


async def test_registering_twice_conflicts(session: AsyncSession) -> None:
    await make_user(session, "Ada")
    with pytest.raises(ConflictError):
        await make_user(session, "Ada")


async def test_authenticate_rejects_a_wrong_password(session: AsyncSession) -> None:
    user = await make_user(session, "Ada")
    with pytest.raises(AuthenticationError):
        await user_service.authenticate(session, user.email, "definitely-not-it")


async def test_authenticate_rejects_a_deactivated_account(session: AsyncSession) -> None:
    user = await make_user(session, "Ada")
    user.is_active = False
    await session.commit()

    with pytest.raises(AuthenticationError, match="deactivated"):
        await user_service.authenticate(session, user.email, TEST_PASSWORD)


async def test_unknown_user_id_is_a_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await user_service.get_by_id(session, uuid.uuid4())


async def test_get_group_sees_members_added_in_the_same_session(
    session: AsyncSession,
) -> None:
    """Regression: the identity map used to hand back a stale member collection.

    ``add_member`` inserts a GroupMember row rather than appending to
    ``group.members``, so re-reading the group has to overwrite what was already
    loaded -- otherwise the very next expense rejects the new member as an outsider.
    """
    ada = await make_user(session, "Ada")
    blaise = await make_user(session, "Blaise")

    group = await group_service.create_group(session, ada, GroupCreate(name="Lisbon"))
    assert {membership.user_id for membership in group.members} == {ada.id}

    await group_service.add_member(session, group, MemberAdd(email=blaise.email))
    refreshed = await group_service.get_group(session, group.id)

    assert {membership.user_id for membership in refreshed.members} == {ada.id, blaise.id}


async def test_creator_is_recorded_as_owner(session: AsyncSession) -> None:
    ada = await make_user(session, "Ada")
    group = await group_service.create_group(session, ada, GroupCreate(name="Lisbon"))

    membership = await group_service.get_membership(session, group.id, ada.id)
    assert membership.role is MemberRole.OWNER
    assert membership.user.display_name == "Ada"


async def test_unknown_group_is_a_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await group_service.get_group(session, uuid.uuid4())
