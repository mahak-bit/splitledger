"""Pydantic request and response models: the public API contract."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from splitledger.models import Expense, GroupMember, MemberRole, Settlement, SplitStrategy
from splitledger.money import to_major_units

T = TypeVar("T")

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern="^[A-Z]{3}$")]


class ORMModel(BaseModel):
    """Base for responses read straight off an ORM instance."""

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """Offset pagination envelope; ``total`` is the unfiltered match count."""

    items: list[T]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- auth


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=128)


class UserRead(ORMModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - a scheme name, not a secret
    expires_in: int = Field(description="Token lifetime in seconds.")


# ------------------------------------------------------------------------- groups


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    currency: CurrencyCode = "USD"


class GroupRead(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    currency: str
    created_by_id: uuid.UUID
    created_at: datetime


class MemberRead(BaseModel):
    user: UserRead
    role: MemberRole
    joined_at: datetime

    @classmethod
    def from_model(cls, membership: GroupMember) -> "MemberRead":
        return cls(
            user=UserRead.model_validate(membership.user),
            role=membership.role,
            joined_at=membership.joined_at,
        )


class GroupDetail(GroupRead):
    members: list[MemberRead]


class MemberAdd(BaseModel):
    email: EmailStr
    role: MemberRole = MemberRole.MEMBER


# ----------------------------------------------------------------------- expenses


class ShareInput(BaseModel):
    """One participant's figure: an exact amount, a percentage, or a share weight."""

    user_id: uuid.UUID
    value: Decimal = Field(gt=0, max_digits=12, decimal_places=4)


class ExpenseCreate(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    amount: PositiveMoney
    paid_by: uuid.UUID
    strategy: SplitStrategy = SplitStrategy.EQUAL
    participants: list[uuid.UUID] | None = Field(
        default=None, description="Required for the equal strategy, forbidden otherwise."
    )
    shares: list[ShareInput] | None = Field(
        default=None, description="Required for every strategy except equal."
    )
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def _check_strategy_inputs(self) -> "ExpenseCreate":
        if self.strategy is SplitStrategy.EQUAL:
            if not self.participants:
                raise ValueError("participants is required for the equal strategy")
            if self.shares:
                raise ValueError("shares must be omitted for the equal strategy")
            if len(set(self.participants)) != len(self.participants):
                raise ValueError("participants must not contain duplicates")
            return self

        if not self.shares:
            raise ValueError(f"shares is required for the {self.strategy.value} strategy")
        if self.participants:
            raise ValueError(f"participants must be omitted for the {self.strategy.value} strategy")
        user_ids = [share.user_id for share in self.shares]
        if len(set(user_ids)) != len(user_ids):
            raise ValueError("each participant may appear only once in shares")

        total = sum((share.value for share in self.shares), Decimal(0))
        if self.strategy is SplitStrategy.PERCENTAGE and total != Decimal(100):
            raise ValueError(f"percentages must add up to 100, got {total}")
        if self.strategy is SplitStrategy.EXACT and total != self.amount:
            raise ValueError(f"exact amounts must add up to {self.amount}, got {total}")
        if self.strategy is SplitStrategy.SHARES and any(
            share.value != share.value.to_integral_value() for share in self.shares
        ):
            raise ValueError("share weights must be whole numbers")
        return self


class ExpenseShareRead(BaseModel):
    user_id: uuid.UUID
    amount: Money


class ExpenseRead(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    description: str
    amount: Money
    currency: str
    paid_by: uuid.UUID
    strategy: SplitStrategy
    occurred_at: datetime
    created_at: datetime
    shares: list[ExpenseShareRead]

    @classmethod
    def from_model(cls, expense: Expense) -> "ExpenseRead":
        return cls(
            id=expense.id,
            group_id=expense.group_id,
            description=expense.description,
            amount=to_major_units(expense.amount_minor),
            currency=expense.currency,
            paid_by=expense.paid_by_id,
            strategy=expense.split_strategy,
            occurred_at=expense.occurred_at,
            created_at=expense.created_at,
            shares=[
                ExpenseShareRead(user_id=share.user_id, amount=to_major_units(share.share_minor))
                for share in sorted(expense.shares, key=lambda share: str(share.user_id))
            ],
        )


# ---------------------------------------------------------- balances and settling


class BalanceRead(BaseModel):
    """A member's position. Positive means the group owes them."""

    user_id: uuid.UUID
    display_name: str
    net: Money


class GroupBalances(BaseModel):
    group_id: uuid.UUID
    currency: str
    balances: list[BalanceRead]
    generated_at: datetime


class TransferRead(BaseModel):
    payer: uuid.UUID
    payee: uuid.UUID
    amount: Money


class SettlementPlanRead(BaseModel):
    group_id: uuid.UUID
    currency: str
    method: Literal["exact", "greedy"] = Field(
        description="exact = provably minimal; greedy = heuristic used on large groups."
    )
    transfer_count: int
    total_amount: Money
    transfers: list[TransferRead]


class SettlementCreate(BaseModel):
    """Record a payment *from the authenticated user* to another member."""

    to_user: uuid.UUID
    amount: PositiveMoney
    note: str | None = Field(default=None, max_length=200)


class SettlementRead(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    from_user: uuid.UUID
    to_user: uuid.UUID
    amount: Money
    note: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, settlement: Settlement) -> "SettlementRead":
        return cls(
            id=settlement.id,
            group_id=settlement.group_id,
            from_user=settlement.from_user_id,
            to_user=settlement.to_user_id,
            amount=to_major_units(settlement.amount_minor),
            note=settlement.note,
            created_at=settlement.created_at,
        )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["up", "down"] | None = None
