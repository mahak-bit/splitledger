"""Tests for the settlement planner, including where greedy is provably worse."""

import random

import pytest

from splitledger.settlement import Transfer, minimum_transfer_count, plan_settlement


def apply(balances: dict[str, int], transfers: list[Transfer[str]]) -> dict[str, int]:
    """Replay a plan against the balances it was built from."""
    result = dict(balances)
    for transfer in transfers:
        result[transfer.payer] = result.get(transfer.payer, 0) + transfer.amount_minor
        result[transfer.payee] = result.get(transfer.payee, 0) - transfer.amount_minor
    return result


def test_empty_ledger_needs_no_transfers() -> None:
    assert plan_settlement({}).transfers == []
    assert plan_settlement({"a": 0, "b": 0}).transfers == []


def test_unbalanced_ledger_is_rejected() -> None:
    with pytest.raises(ValueError, match="sum to zero"):
        plan_settlement({"a": 100, "b": -50})


def test_two_party_debt_is_a_single_transfer() -> None:
    plan = plan_settlement({"a": -250, "b": 250})
    assert plan.transfers == [Transfer(payer="a", payee="b", amount_minor=250)]
    assert plan.method == "exact"


def test_plan_clears_every_balance() -> None:
    balances = {"a": 500, "b": -300, "c": -200, "d": 700, "e": -700}
    plan = plan_settlement(balances)
    assert set(apply(balances, plan.transfers).values()) == {0}


def test_exact_solver_beats_greedy_on_a_separable_ledger() -> None:
    """{b, e} and {a, c, d} are independently zero-sum; greedy chains them together."""
    balances = {"a": 7, "b": 6, "c": -3, "d": -4, "e": -6}

    optimal = plan_settlement(balances, exact_threshold=12)
    greedy = plan_settlement(balances, exact_threshold=4)

    assert optimal.method == "exact"
    assert optimal.transfer_count == 3
    assert optimal.transfer_count == minimum_transfer_count(balances)

    assert greedy.method == "greedy"
    assert greedy.transfer_count == 4

    # Both are correct; only the count differs.
    assert set(apply(balances, optimal.transfers).values()) == {0}
    assert set(apply(balances, greedy.transfers).values()) == {0}


def test_members_who_are_square_are_left_out() -> None:
    plan = plan_settlement({"a": 100, "b": -100, "c": 0})
    involved = {transfer.payer for transfer in plan.transfers} | {
        transfer.payee for transfer in plan.transfers
    }
    assert "c" not in involved


def test_no_transfer_is_empty_or_reflexive() -> None:
    balances = {"a": 900, "b": -100, "c": -300, "d": -500}
    for transfer in plan_settlement(balances).transfers:
        assert transfer.amount_minor > 0
        assert transfer.payer != transfer.payee


@pytest.mark.parametrize("seed", range(20))
def test_random_ledgers_settle_within_the_upper_bound(seed: int) -> None:
    rng = random.Random(seed)
    people = [f"p{index}" for index in range(rng.randint(2, 9))]
    balances = {person: rng.randint(-5000, 5000) for person in people}
    # Force the ledger to balance by absorbing the residual into one member.
    balances[people[0]] -= sum(balances.values())

    plan = plan_settlement(balances)
    active = sum(1 for value in balances.values() if value != 0)

    assert set(apply(balances, plan.transfers).values()) == {0}
    assert plan.transfer_count <= max(active - 1, 0)
    assert plan.transfer_count == minimum_transfer_count(balances)


def test_large_groups_fall_back_to_greedy_but_still_settle() -> None:
    rng = random.Random(99)
    people = [f"p{index}" for index in range(30)]
    balances = {person: rng.randint(-1000, 1000) for person in people}
    balances[people[0]] -= sum(balances.values())

    plan = plan_settlement(balances, exact_threshold=12)
    active = sum(1 for value in balances.values() if value != 0)

    assert plan.method == "greedy"
    assert set(apply(balances, plan.transfers).values()) == {0}
    assert plan.transfer_count <= active - 1


def test_total_moved_never_exceeds_the_debt() -> None:
    balances = {"a": 1000, "b": 250, "c": -600, "d": -650}
    plan = plan_settlement(balances)
    owed = sum(value for value in balances.values() if value > 0)
    assert plan.total_minor == owed
