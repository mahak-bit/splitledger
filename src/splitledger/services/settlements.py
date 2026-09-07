"""Recording real-world payments between members."""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from splitledger.errors import DomainValidationError
from splitledger.models import Group, Settlement, User
from splitledger.money import to_minor_units
from splitledger.schemas import SettlementCreate


async def record_settlement(
    session: AsyncSession,
    group: Group,
    actor: User,
    payload: SettlementCreate,
) -> Settlement:
    """Record that the caller paid another member back.

    Only the payer can log a payment. Letting anyone credit themselves from
    somebody else's account would make the ledger trivially forgeable.
    """
    member_ids = {membership.user_id for membership in group.members}
    if payload.to_user not in member_ids:
        raise DomainValidationError(
            "The recipient must be a member of the group.",
            details={"to_user": str(payload.to_user)},
        )
    if payload.to_user == actor.id:
        raise DomainValidationError("A settlement needs two different people.")

    settlement = Settlement(
        group_id=group.id,
        from_user_id=actor.id,
        to_user_id=payload.to_user,
        amount_minor=to_minor_units(payload.amount),
        note=payload.note,
    )
    session.add(settlement)
    await session.commit()
    await session.refresh(settlement)
    return settlement


async def list_settlements(
    session: AsyncSession,
    group_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    user_id: uuid.UUID | None = None,
) -> tuple[list[Settlement], int]:
    """Page through a group's payments, most recent first."""
    filters = [Settlement.group_id == group_id]
    if user_id is not None:
        filters.append(or_(Settlement.from_user_id == user_id, Settlement.to_user_id == user_id))

    total = await session.scalar(select(func.count()).select_from(Settlement).where(*filters))
    result = await session.execute(
        select(Settlement)
        .where(*filters)
        .order_by(Settlement.created_at.desc(), Settlement.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars()), int(total or 0)
