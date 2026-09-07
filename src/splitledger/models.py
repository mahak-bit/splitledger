"""ORM models: users, groups, expenses with their shares, and recorded payments."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from splitledger.db import Base


def utcnow() -> datetime:
    """Timezone-aware now, so no stored timestamp is ever ambiguous."""
    return datetime.now(timezone.utc)


class SplitStrategy(str, enum.Enum):
    """How an expense total is divided among its participants."""

    EQUAL = "equal"
    EXACT = "exact"
    PERCENTAGE = "percentage"
    SHARES = "shares"


class MemberRole(str, enum.Enum):
    """Owners can manage membership; members can only add expenses."""

    OWNER = "owner"
    MEMBER = "member"


def _enum_column(enum_class: type[enum.Enum]) -> Enum:
    """Store enums as their string *values* in a VARCHAR, for backend portability."""
    return Enum(
        enum_class,
        native_enum=False,
        length=20,
        values_callable=lambda members: [str(member.value) for member in members],
    )


class User(Base):
    """A person who can belong to groups and be charged a share of an expense."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list["GroupMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Group(Base):
    """A shared ledger: a trip, a flat, a recurring dinner club."""

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text(), default=None)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="selectin"
    )
    expenses: Mapped[list["Expense"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class GroupMember(Base):
    """Join table carrying the role, so membership is a first-class record."""

    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MemberRole] = mapped_column(_enum_column(MemberRole), default=MemberRole.MEMBER)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    group: Mapped["Group"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships", lazy="joined")


class Expense(Base):
    """One payment made by one member on behalf of some subset of the group."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index("ix_expenses_group_occurred_at", "group_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(String(200))
    amount_minor: Mapped[int] = mapped_column(BigInteger())
    currency: Mapped[str] = mapped_column(String(3))
    paid_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    split_strategy: Mapped[SplitStrategy] = mapped_column(_enum_column(SplitStrategy))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    group: Mapped["Group"] = relationship(back_populates="expenses")
    paid_by: Mapped["User"] = relationship(foreign_keys="Expense.paid_by_id", lazy="joined")
    shares: Mapped[list["ExpenseShare"]] = relationship(
        back_populates="expense", cascade="all, delete-orphan", lazy="selectin"
    )


class ExpenseShare(Base):
    """What one participant owes for one expense, already resolved to minor units."""

    __tablename__ = "expense_shares"
    __table_args__ = (
        UniqueConstraint("expense_id", "user_id"),
        CheckConstraint("share_minor >= 0", name="share_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    share_minor: Mapped[int] = mapped_column(BigInteger())

    expense: Mapped["Expense"] = relationship(back_populates="shares")


class Settlement(Base):
    """A real-world payment between two members, recorded to close out a debt."""

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("from_user_id <> to_user_id", name="distinct_parties"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    from_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    to_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount_minor: Mapped[int] = mapped_column(BigInteger())
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
