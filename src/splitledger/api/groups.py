"""Groups, membership, balances, and the settlement plan."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from splitledger.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    GroupAccess,
    OwnerAccess,
    PageParams,
)
from splitledger.models import Group
from splitledger.money import to_major_units
from splitledger.schemas import (
    GroupBalances,
    GroupCreate,
    GroupDetail,
    GroupRead,
    MemberAdd,
    MemberRead,
    Page,
    SettlementPlanRead,
    TransferRead,
)
from splitledger.services import balances as balance_service
from splitledger.services import groups as group_service

router = APIRouter(prefix="/groups", tags=["groups"])


async def _detail(session: AsyncSession, group: Group) -> GroupDetail:
    members = await group_service.list_members(session, group.id)
    return GroupDetail(
        **GroupRead.model_validate(group).model_dump(),
        members=[MemberRead.from_model(membership) for membership in members],
    )


@router.post(
    "",
    response_model=GroupDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a group",
)
async def create_group(
    payload: GroupCreate,
    session: DbSession,
    user: CurrentUser,
) -> GroupDetail:
    group = await group_service.create_group(session, user, payload)
    return await _detail(session, group)


@router.get("", response_model=Page[GroupRead], summary="List my groups")
async def list_groups(
    session: DbSession,
    user: CurrentUser,
    page: PageParams,
) -> Page[GroupRead]:
    groups, total = await group_service.list_for_user(
        session, user, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[GroupRead.model_validate(group) for group in groups],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{group_id}", response_model=GroupDetail, summary="Read a group")
async def read_group(context: GroupAccess, session: DbSession) -> GroupDetail:
    return await _detail(session, context.group)


@router.post(
    "/{group_id}/members",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member (owners only)",
)
async def add_member(
    payload: MemberAdd,
    context: OwnerAccess,
    session: DbSession,
) -> MemberRead:
    membership = await group_service.add_member(session, context.group, payload)
    return MemberRead.from_model(membership)


@router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (owners only, once they are square)",
)
async def remove_member(
    user_id: Annotated[uuid.UUID, Path(description="Member to remove.")],
    context: OwnerAccess,
    session: DbSession,
) -> None:
    await group_service.remove_member(session, context.group, user_id)


@router.get(
    "/{group_id}/balances",
    response_model=GroupBalances,
    summary="Net position of every member",
)
async def read_balances(context: GroupAccess, session: DbSession) -> GroupBalances:
    report = await balance_service.balance_report(session, context.group)
    return GroupBalances(
        group_id=context.group.id,
        currency=context.group.currency,
        balances=report,
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/{group_id}/settlement-plan",
    response_model=SettlementPlanRead,
    summary="The fewest payments that clear the ledger",
)
async def read_settlement_plan(
    context: GroupAccess,
    session: DbSession,
    settings: AppSettings,
) -> SettlementPlanRead:
    """Return who should pay whom, and how much, to zero out every balance.

    Groups at or below the configured size get a provably minimal plan; larger
    groups fall back to a greedy plan, flagged as such in ``method``.
    """
    plan = await balance_service.plan_for_group(
        session, context.group, exact_threshold=settings.settlement_exact_threshold
    )
    return SettlementPlanRead(
        group_id=context.group.id,
        currency=context.group.currency,
        method=plan.method,
        transfer_count=plan.transfer_count,
        total_amount=to_major_units(plan.total_minor),
        transfers=[
            TransferRead(
                payer=transfer.payer,
                payee=transfer.payee,
                amount=to_major_units(transfer.amount_minor),
            )
            for transfer in plan.transfers
        ],
    )
