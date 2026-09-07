"""Derive per-member balances from the raw ledger, and plan how to clear them.

Balances are always recomputed from expenses and settlements rather than cached
on a row. That keeps the ledger the single source of truth, so a deleted expense
can never leave a stale total behind.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from splitledger.models import Expense, ExpenseShare, Group, GroupMember, Settlement, User
from splitledger.money import to_major_units
from splitledger.schemas import BalanceRead
from splitledger.settlement import SettlementPlan, plan_settlement


async def compute_balances(session: AsyncSession, group_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Net position per member in minor units; positive means the group owes them.

    net = what they paid for - what they owe + what they have since paid back
          - what they have since been paid.

    Every expense adds its total to one payer and subtracts the same total
    across its shares, and every settlement moves one amount between two people,
    so the returned values always sum to zero.
    """
    balances: dict[uuid.UUID, int] = {}

    members = await session.execute(
        select(GroupMember.user_id).where(GroupMember.group_id == group_id)
    )
    for user_id in members.scalars():
        balances[user_id] = 0

    def apply(user_id: uuid.UUID, delta: int) -> None:
        balances[user_id] = balances.get(user_id, 0) + delta

    paid = await session.execute(
        select(Expense.paid_by_id, func.sum(Expense.amount_minor))
        .where(Expense.group_id == group_id)
        .group_by(Expense.paid_by_id)
    )
    for user_id, total in paid:
        apply(user_id, int(total or 0))

    owed = await session.execute(
        select(ExpenseShare.user_id, func.sum(ExpenseShare.share_minor))
        .join(Expense, Expense.id == ExpenseShare.expense_id)
        .where(Expense.group_id == group_id)
        .group_by(ExpenseShare.user_id)
    )
    for user_id, total in owed:
        apply(user_id, -int(total or 0))

    sent = await session.execute(
        select(Settlement.from_user_id, func.sum(Settlement.amount_minor))
        .where(Settlement.group_id == group_id)
        .group_by(Settlement.from_user_id)
    )
    for user_id, total in sent:
        apply(user_id, int(total or 0))

    received = await session.execute(
        select(Settlement.to_user_id, func.sum(Settlement.amount_minor))
        .where(Settlement.group_id == group_id)
        .group_by(Settlement.to_user_id)
    )
    for user_id, total in received:
        apply(user_id, -int(total or 0))

    return balances


async def balance_report(session: AsyncSession, group: Group) -> list[BalanceRead]:
    """Balances decorated with display names, ordered richest-first for readability."""
    balances = await compute_balances(session, group.id)
    if not balances:
        return []

    rows = await session.execute(
        select(User.id, User.display_name).where(User.id.in_(list(balances)))
    )
    names: dict[uuid.UUID, str] = dict(rows.tuples().all())

    report = [
        BalanceRead(
            user_id=user_id,
            display_name=names.get(user_id, "unknown"),
            net=to_major_units(net_minor),
        )
        for user_id, net_minor in balances.items()
    ]
    report.sort(key=lambda entry: (-entry.net, entry.display_name))
    return report


async def plan_for_group(
    session: AsyncSession,
    group: Group,
    *,
    exact_threshold: int,
) -> SettlementPlan[uuid.UUID]:
    """Compute the payments that would zero out this group's ledger."""
    balances = await compute_balances(session, group.id)
    return plan_settlement(balances, exact_threshold=exact_threshold)
