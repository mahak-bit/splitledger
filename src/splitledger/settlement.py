"""Turn a group's net balances into the fewest possible payments.

A ledger collapses to one integer per member: their net balance in minor units.
Positive means the group owes them, negative means they owe the group, and
because every expense is fully allocated the balances always sum to zero.

Two facts drive the implementation:

1. Any zero-sum set of ``k`` people can be settled in exactly ``k - 1``
   transfers: repeatedly pay somebody off completely and drop them.
2. In *any* valid settlement, each connected component of the payment graph is
   itself zero-sum, so a settlement whose graph has ``c`` components uses at
   least ``n - c`` transfers.

Together these give the minimum: ``n - g``, where ``g`` is the largest number of
disjoint zero-sum subsets the members can be partitioned into. Maximising ``g``
contains the PARTITION problem, so it is NP-hard. We solve it exactly with a
subset-sum DP over bitmasks -- ``O(3^n)``, comfortable up to about a dozen
members -- and above that fall back to greedily matching the largest creditor
with the largest debtor, which never needs more than ``n - 1`` transfers.

Worked example (minor units)::

    A: +500   B: -300   C: -200   D: +700   E: -700

Naive greedy over the whole set pairs D with E, then A with B and C, and gets
lucky at 3 transfers. The exact solver *proves* 3 is optimal by finding the two
independent zero-sum groups {D, E} and {A, B, C}: 5 members, 2 groups, 3
transfers. On ledgers with clean internal sub-groups the difference is real --
greedy can chain everyone together where the optimum keeps them apart.
"""

import heapq
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

K = TypeVar("K", bound=Hashable)

Method = Literal["exact", "greedy"]

DEFAULT_EXACT_THRESHOLD = 12


@dataclass(frozen=True)
class Transfer(Generic[K]):
    """A single payment: payer hands amount_minor to payee."""

    payer: K
    payee: K
    amount_minor: int


@dataclass(frozen=True)
class SettlementPlan(Generic[K]):
    """The transfers that clear a ledger, plus how they were derived."""

    transfers: list[Transfer[K]]
    method: Method

    @property
    def transfer_count(self) -> int:
        return len(self.transfers)

    @property
    def total_minor(self) -> int:
        return sum(transfer.amount_minor for transfer in self.transfers)


def plan_settlement(
    balances: Mapping[K, int],
    *,
    exact_threshold: int = DEFAULT_EXACT_THRESHOLD,
) -> SettlementPlan[K]:
    """Build the payment plan that clears ``balances``.

    Raises:
        ValueError: if the balances do not sum to zero, which means the caller
            handed us an inconsistent ledger rather than a rounding artefact.
    """
    residual = sum(balances.values())
    if residual != 0:
        msg = f"Balances must sum to zero to be settleable (off by {residual} minor units)."
        raise ValueError(msg)

    active = [(key, value) for key, value in balances.items() if value != 0]
    if not active:
        return SettlementPlan(transfers=[], method="exact")

    if len(active) <= exact_threshold:
        method: Method = "exact"
        groups = _max_zero_sum_partition([value for _, value in active])
    else:
        method = "greedy"
        groups = [list(range(len(active)))]

    transfers: list[Transfer[K]] = []
    for group in groups:
        transfers.extend(_settle_component([active[index] for index in group]))
    return SettlementPlan(transfers=transfers, method=method)


def minimum_transfer_count(balances: Mapping[K, int]) -> int:
    """The provably minimal number of transfers, used by tests and benchmarks."""
    active = [value for value in balances.values() if value != 0]
    if not active:
        return 0
    return len(active) - len(_max_zero_sum_partition(active))


def _settle_component(entries: Sequence[tuple[K, int]]) -> list[Transfer[K]]:
    """Greedily clear one zero-sum component in ``len(entries) - 1`` transfers.

    Each round pairs the biggest creditor with the biggest debtor, which always
    zeroes at least one of them, so the loop shrinks the problem every step.
    """
    # Both heaps are min-heaps over negative magnitudes, so the largest amount
    # pops first. The positional index keeps the ordering total and deterministic
    # even when the keys themselves are not comparable.
    creditors: list[tuple[int, int, K]] = []
    debtors: list[tuple[int, int, K]] = []
    for index, (key, amount) in enumerate(entries):
        if amount > 0:
            heapq.heappush(creditors, (-amount, index, key))
        elif amount < 0:
            heapq.heappush(debtors, (amount, index, key))

    transfers: list[Transfer[K]] = []
    while creditors and debtors:
        neg_credit, credit_index, creditor = heapq.heappop(creditors)
        neg_debt, debt_index, debtor = heapq.heappop(debtors)

        credit = -neg_credit
        debt = -neg_debt
        amount = min(credit, debt)
        transfers.append(Transfer(payer=debtor, payee=creditor, amount_minor=amount))

        if credit > amount:
            heapq.heappush(creditors, (-(credit - amount), credit_index, creditor))
        if debt > amount:
            heapq.heappush(debtors, (-(debt - amount), debt_index, debtor))

    return transfers


def _max_zero_sum_partition(values: Sequence[int]) -> list[list[int]]:
    """Split indices into as many disjoint zero-sum groups as possible.

    ``best[mask]`` holds the most zero-sum groups the members in ``mask`` can
    form. Every candidate subset is forced to contain the lowest set bit of
    ``mask``, which discards permutations of the same partition and keeps the
    submask walk at ``O(3^n)`` overall.
    """
    size = len(values)
    full = (1 << size) - 1

    subset_sum = [0] * (1 << size)
    for mask in range(1, 1 << size):
        lowest = mask & -mask
        subset_sum[mask] = subset_sum[mask ^ lowest] + values[lowest.bit_length() - 1]

    best = [0] * (1 << size)
    choice = [0] * (1 << size)
    for mask in range(1, 1 << size):
        lowest = mask & -mask
        submask = mask
        while submask:
            if submask & lowest and subset_sum[submask] == 0:
                candidate = best[mask ^ submask] + 1
                if candidate > best[mask]:
                    best[mask] = candidate
                    choice[mask] = submask
            submask = (submask - 1) & mask

    groups: list[list[int]] = []
    mask = full
    while mask:
        # Only zero-sum masks are ever reached, so a choice always exists here.
        picked = choice[mask]
        groups.append([index for index in range(size) if picked >> index & 1])
        mask ^= picked
    return groups
