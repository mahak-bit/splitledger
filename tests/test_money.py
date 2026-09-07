"""The allocator is the load-bearing piece of the ledger: it must never lose a cent."""

import random
from decimal import Decimal

import pytest

from splitledger.errors import DomainValidationError
from splitledger.money import (
    allocate,
    split_by_percentage,
    split_by_shares,
    split_equally,
    to_major_units,
    to_minor_units,
    validate_exact_split,
)


def test_minor_unit_round_trip() -> None:
    assert to_minor_units(Decimal("12.34")) == 1234
    assert to_major_units(1234) == Decimal("12.34")
    assert to_major_units(to_minor_units(Decimal("0.05"))) == Decimal("0.05")


def test_minor_units_round_half_up() -> None:
    assert to_minor_units(Decimal("1.005")) == 101
    assert to_minor_units(Decimal("1.004")) == 100


def test_equal_split_hands_out_the_odd_cents() -> None:
    # 10.00 three ways is 3.3333...; somebody has to absorb the extra cent.
    assert split_equally(1000, 3) == [334, 333, 333]
    assert sum(split_equally(1000, 3)) == 1000


def test_equal_split_is_deterministic() -> None:
    assert split_equally(1000, 3) == split_equally(1000, 3)
    assert split_equally(100, 7) == [15, 15, 14, 14, 14, 14, 14]


def test_share_split_respects_weights() -> None:
    assert split_by_shares(2400, [2, 1, 1]) == [1200, 600, 600]


def test_percentage_split_must_total_one_hundred() -> None:
    assert split_by_percentage(10000, [Decimal(50), Decimal(30), Decimal(20)]) == [
        5000,
        3000,
        2000,
    ]
    with pytest.raises(DomainValidationError):
        split_by_percentage(10000, [Decimal(50), Decimal(30)])


def test_percentage_split_with_repeating_thirds() -> None:
    thirds = [Decimal("33.3333"), Decimal("33.3333"), Decimal("33.3334")]
    shares = split_by_percentage(10000, thirds)
    assert sum(shares) == 10000


def test_exact_split_rejects_a_mismatched_total() -> None:
    assert validate_exact_split(1000, [400, 600]) == [400, 600]
    with pytest.raises(DomainValidationError) as caught:
        validate_exact_split(1000, [400, 500])
    assert caught.value.details["difference_minor"] == -100


def test_allocate_rejects_degenerate_input() -> None:
    with pytest.raises(DomainValidationError):
        allocate(100, [])
    with pytest.raises(DomainValidationError):
        allocate(100, [0, 0])
    with pytest.raises(DomainValidationError):
        allocate(100, [-1, 2])


def test_allocate_handles_negative_totals_symmetrically() -> None:
    assert allocate(-1000, [1, 1, 1]) == [-334, -333, -333]


@pytest.mark.parametrize("seed", range(25))
def test_allocation_always_sums_to_the_total(seed: int) -> None:
    """Randomised check of the one invariant the ledger cannot survive without."""
    rng = random.Random(seed)
    total = rng.randint(0, 10_000_000)
    weights = [rng.randint(1, 50) for _ in range(rng.randint(1, 12))]

    shares = allocate(total, weights)

    assert sum(shares) == total
    assert len(shares) == len(weights)
    assert all(share >= 0 for share in shares)
    # Nobody is off by more than one minor unit from their exact fractional share.
    total_weight = sum(weights)
    for share, weight in zip(shares, weights, strict=True):
        assert abs(share * total_weight - total * weight) <= total_weight
