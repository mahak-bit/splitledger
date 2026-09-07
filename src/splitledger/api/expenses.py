"""Expense CRUD, scoped to a group the caller belongs to."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from splitledger.deps import DbSession, GroupAccess, PageParams
from splitledger.errors import PermissionDeniedError
from splitledger.models import MemberRole
from splitledger.schemas import ExpenseCreate, ExpenseRead, Page
from splitledger.services import expenses as expense_service

router = APIRouter(prefix="/groups/{group_id}/expenses", tags=["expenses"])


@router.post(
    "",
    response_model=ExpenseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an expense",
)
async def create_expense(
    payload: ExpenseCreate,
    context: GroupAccess,
    session: DbSession,
) -> ExpenseRead:
    """Split an amount across group members using the requested strategy.

    Whichever strategy is used, the resulting shares are guaranteed to add back
    up to the expense total -- no cent is created or lost by rounding.
    """
    expense = await expense_service.create_expense(session, context.group, context.user, payload)
    return ExpenseRead.from_model(expense)


@router.get("", response_model=Page[ExpenseRead], summary="List expenses")
async def list_expenses(
    context: GroupAccess,
    session: DbSession,
    page: PageParams,
    paid_by: Annotated[uuid.UUID | None, Query(description="Filter by payer.")] = None,
) -> Page[ExpenseRead]:
    expenses, total = await expense_service.list_expenses(
        session,
        context.group.id,
        limit=page.limit,
        offset=page.offset,
        paid_by=paid_by,
    )
    return Page(
        items=[ExpenseRead.from_model(expense) for expense in expenses],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{expense_id}", response_model=ExpenseRead, summary="Read an expense")
async def read_expense(
    expense_id: Annotated[uuid.UUID, Path(description="Expense identifier.")],
    context: GroupAccess,
    session: DbSession,
) -> ExpenseRead:
    expense = await expense_service.get_expense(session, context.group.id, expense_id)
    return ExpenseRead.from_model(expense)


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an expense",
)
async def delete_expense(
    expense_id: Annotated[uuid.UUID, Path(description="Expense identifier.")],
    context: GroupAccess,
    session: DbSession,
) -> None:
    """Only the member who recorded an expense, or a group owner, may delete it."""
    expense = await expense_service.get_expense(session, context.group.id, expense_id)
    is_owner = context.membership.role is MemberRole.OWNER
    if expense.created_by_id != context.user.id and not is_owner:
        raise PermissionDeniedError(
            "Only the member who recorded this expense, or a group owner, can delete it."
        )
    await expense_service.delete_expense(session, expense)
