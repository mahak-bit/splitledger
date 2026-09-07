"""Money arithmetic and deterministic split allocation.

Every monetary value inside the system is an integer count of *minor units*
(cents, for USD). Floating point is never used for money: it cannot represent
0.10 exactly, and repeated rounding quietly leaks value out of a ledger that is
required to sum to zero. Decimals appear only at the API boundary.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from splitledger.errors import DomainValidationError

MINOR_UNITS_PER_MAJOR = 100
_CENTS = Decimal("0.01")
# Percentages become integer weights at four decimal places, so a value like
# 33.3333% stays exact all the way through the allocator.
_PERCENT_SCALE = 10_000


def to_minor_units(amount: Decimal) -> int:
    """Convert a decimal major-unit amount (12.34) into minor units (1234)."""
    try:
        quantized = amount.quantize(_CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ArithmeticError) as exc:
        raise DomainValidationError(f"Amount {amount} is not a usable money value.") from exc
    return int(quantized.scaleb(2))


def to_major_units(minor: int) -> Decimal:
    """Convert minor units back into a two-decimal major-unit amount."""
    return (Decimal(minor) / MINOR_UNITS_PER_MAJOR).quantize(_CENTS)


def allocate(total_minor: int, weights: Sequence[int]) -> list[int]:
    """Divide a total across weights so the parts sum to it *exactly*.

    This is the largest-remainder (Hamilton) method: everyone gets their floored
    share, then the leftover minor units go to whoever was rounded down hardest.
    Ties break on position, so results are deterministic -- splitting 10.00 three
    ways always gives 3.34 / 3.33 / 3.33, never a cent that drifts between runs.
    """
    if not weights:
        raise DomainValidationError("A split needs at least one participant.")
    if any(weight < 0 for weight in weights):
        raise DomainValidationError("Split weights must not be negative.")

    total_weight = sum(weights)
    if total_weight <= 0:
        raise DomainValidationError("Split weights must sum to a positive value.")

    sign = -1 if total_minor < 0 else 1
    magnitude = abs(total_minor)

    shares = [magnitude * weight // total_weight for weight in weights]
    remainders = [magnitude * weight % total_weight for weight in weights]

    leftover = magnitude - sum(shares)
    order = sorted(range(len(weights)), key=lambda index: (-remainders[index], index))
    for index in order[:leftover]:
        shares[index] += 1

    return [sign * share for share in shares]


def split_equally(total_minor: int, participant_count: int) -> list[int]:
    """Even split, with any leftover cents handed out one each from the front."""
    if participant_count <= 0:
        raise DomainValidationError("A split needs at least one participant.")
    return allocate(total_minor, [1] * participant_count)


def split_by_shares(total_minor: int, shares: Sequence[int]) -> list[int]:
    """Weighted split, e.g. [2, 1, 1] for a couple sharing a bill with two singles."""
    if any(share <= 0 for share in shares):
        raise DomainValidationError("Share weights must be positive integers.")
    return allocate(total_minor, shares)


def split_by_percentage(total_minor: int, percentages: Sequence[Decimal]) -> list[int]:
    """Percentage split; the percentages must add up to exactly 100."""
    if any(percentage < 0 for percentage in percentages):
        raise DomainValidationError("Percentages must not be negative.")
    if sum(percentages, Decimal(0)) != Decimal(100):
        raise DomainValidationError("Percentages must add up to exactly 100.")
    weights = [
        int((percentage * _PERCENT_SCALE).to_integral_value(ROUND_HALF_UP))
        for percentage in percentages
    ]
    return allocate(total_minor, weights)


def validate_exact_split(total_minor: int, amounts: Sequence[int]) -> list[int]:
    """Pass caller-supplied amounts through once they are proven to sum to the total."""
    if any(amount < 0 for amount in amounts):
        raise DomainValidationError("Exact split amounts must not be negative.")
    provided = sum(amounts)
    if provided != total_minor:
        raise DomainValidationError(
            "Exact split amounts must sum to the expense total.",
            details={
                "expected_minor": total_minor,
                "provided_minor": provided,
                "difference_minor": provided - total_minor,
            },
        )
    return list(amounts)
