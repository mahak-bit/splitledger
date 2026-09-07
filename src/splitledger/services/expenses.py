"""Recording expenses: validate participants, then resolve the split to cents."""

import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from splitledger.errors import DomainValidationError, NotFoundError
from splitledger.models import Expense, ExpenseShare, Group, SplitStrategy, User, utcnow
from splitledger.money import (
    split_by_percentage,
    split_by_shares,
    split_equally,
    to_minor_units,
    validate_exact_split,
)
from splitledger.schemas import ExpenseCreate


def _as_utc(moment: datetime | None) -> datetime:
    """Accept naive timestamps from clients, but store everything in UTC."""
    if moment is None:
        return utcnow()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _require_members(user_ids: Iterable[uuid.UUID], member_ids: set[uuid.UUID]) -> None:
    outsiders = sorted(str(user_id) for user_id in user_ids if user_id not in member_ids)
    if outsiders:
        raise DomainValidationError(
            "Every participant must be a member of the group.",
            details={"non_members": outsiders},
        )


def resolve_split(payload: ExpenseCreate, amount_minor: int) -> tuple[list[uuid.UUID], list[int]]:
    """Turn the requested strategy into concrete per-participant amounts.

    The schema has already checked the shape of the payload, so this only has to
    pick an allocator. Whichever branch runs, the returned amounts sum exactly
    to ``amount_minor``.
    """
    if payload.strategy is SplitStrategy.EQUAL:
        participants = list(payload.participants or [])
        return participants, split_equally(amount_minor, len(participants))

    inputs = payload.shares or []
    participants = [share.user_id for share in inputs]
    values = [share.value for share in inputs]

    if payload.strategy is SplitStrategy.PERCENTAGE:
        return participants, split_by_percentage(amount_minor, values)
    if payload.strategy is SplitStrategy.SHARES:
        return participants, split_by_shares(amount_minor, [int(value) for value in values])
    return participants, validate_exact_split(
        amount_minor, [to_minor_units(value) for value in values]
    )


async def create_expense(
    session: AsyncSession,
    group: Group,
    actor: User,
    payload: ExpenseCreate,
) -> Expense:
    """Add an expense to a group, splitting it across the requested participants."""
    member_ids = {membership.user_id for membership in group.members}
    if payload.paid_by not in member_ids:
        raise DomainValidationError(
            "The payer must be a member of the group.",
            details={"paid_by": str(payload.paid_by)},
        )

    amount_minor = to_minor_units(payload.amount)
    participants, amounts = resolve_split(payload, amount_minor)
    _require_members(participants, member_ids)

    expense = Expense(
        group_id=group.id,
        description=payload.description.strip(),
        amount_minor=amount_minor,
        currency=group.currency,
        paid_by_id=payload.paid_by,
        created_by_id=actor.id,
        split_strategy=payload.strategy,
        occurred_at=_as_utc(payload.occurred_at),
    )
    expense.shares = [
        ExpenseShare(user_id=user_id, share_minor=share_minor)
        for user_id, share_minor in zip(participants, amounts, strict=True)
    ]

    session.add(expense)
    await session.commit()
    return await get_expense(session, group.id, expense.id)


async def get_expense(
    session: AsyncSession,
    group_id: uuid.UUID,
    expense_id: uuid.UUID,
) -> Expense:
    result = await session.execute(
        select(Expense)
        .options(selectinload(Expense.shares))
        .where(Expense.id == expense_id, Expense.group_id == group_id)
    )
    expense = result.scalar_one_or_none()
    if expense is None:
        raise NotFoundError(f"No expense with id {expense_id} in this group.")
    return expense


async def list_expenses(
    session: AsyncSession,
    group_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    paid_by: uuid.UUID | None = None,
) -> tuple[list[Expense], int]:
    """Page through a group's expenses, most recent first."""
    filters = [Expense.group_id == group_id]
    if paid_by is not None:
        filters.append(Expense.paid_by_id == paid_by)

    total = await session.scalar(select(func.count()).select_from(Expense).where(*filters))
    result = await session.execute(
        select(Expense)
        .options(selectinload(Expense.shares))
        .where(*filters)
        .order_by(Expense.occurred_at.desc(), Expense.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().unique()), int(total or 0)


async def delete_expense(session: AsyncSession, expense: Expense) -> None:
    """Delete an expense; its shares go with it via cascade."""
    await session.delete(expense)
    await session.commit()
