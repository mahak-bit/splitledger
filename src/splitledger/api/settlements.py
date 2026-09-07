"""Recording payments that members have actually made to each other."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from splitledger.deps import DbSession, GroupAccess, PageParams
from splitledger.schemas import Page, SettlementCreate, SettlementRead
from splitledger.services import settlements as settlement_service

router = APIRouter(prefix="/groups/{group_id}/settlements", tags=["settlements"])


@router.post(
    "",
    response_model=SettlementRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment you made",
)
async def create_settlement(
    payload: SettlementCreate,
    context: GroupAccess,
    session: DbSession,
) -> SettlementRead:
    """Log money you sent to another member; the payer is always the caller."""
    settlement = await settlement_service.record_settlement(
        session, context.group, context.user, payload
    )
    return SettlementRead.from_model(settlement)


@router.get("", response_model=Page[SettlementRead], summary="List payments")
async def list_settlements(
    context: GroupAccess,
    session: DbSession,
    page: PageParams,
    user_id: Annotated[
        uuid.UUID | None, Query(description="Only payments involving this member.")
    ] = None,
) -> Page[SettlementRead]:
    settlements, total = await settlement_service.list_settlements(
        session,
        context.group.id,
        limit=page.limit,
        offset=page.offset,
        user_id=user_id,
    )
    return Page(
        items=[SettlementRead.from_model(settlement) for settlement in settlements],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
