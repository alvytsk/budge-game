import random
from dataclasses import replace

import pytest

from podvinsya.domain import check_invariants
from podvinsya.domain.actions import DeclareAttack, JudgeCorrect, StartDuel
from podvinsya.domain.board import Cell
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.rules import group_containing, legal_targets
from podvinsya.domain.state import Group, MatchState, MatchStatus

from .conftest import IMAGE_POOL, at, build_running_state


def _play_one_duel(state: MatchState, rng: random.Random, clock: float) -> tuple[MatchState, float]:
    attacker = state.current_player()
    options = [
        (group.id, target)
        for group in state.groups.values()
        if group.owner == attacker
        for target in sorted(legal_targets(state, group.id))
    ]
    assert options, "a player who does not own the whole board always has a legal target"
    attacking, defending = rng.choice(options)

    ctx = DecisionContext(now=at(clock), image_order=IMAGE_POOL)
    state = fold(state, decide(state, DeclareAttack(attacking, defending), ctx))
    check_invariants(state)

    state = fold(state, decide(state, StartDuel(), DecisionContext(now=at(clock))))
    duel = state.duel
    assert duel is not None

    if rng.random() < 0.5:
        # A cheap correct answer hands the turn to the defender, so it is the
        # defender who then runs out and the attacker who wins. Without this
        # branch the attacker would lose every single duel and the test would
        # only ever exercise one of the two outcomes.
        clock += 1
        state = fold(state, decide(state, JudgeCorrect(), DecisionContext(now=at(clock))))
        check_invariants(state)
        duel = state.duel
        assert duel is not None

    # Hand the clock straight past the answering side's remaining budget.
    clock += duel.budgets.get(duel.answering) / 1000 + 1
    state = fold(state, decide(state, JudgeCorrect(), DecisionContext(now=at(clock))))
    check_invariants(state)
    return state, clock + 1


@pytest.mark.parametrize("seed", range(25))
def test_invariants_hold_across_a_whole_random_match(seed: int) -> None:
    rng = random.Random(seed)
    state, _ = build_running_state(4)
    check_invariants(state)

    clock = 0.0
    duels = 0
    while state.status is MatchStatus.RUNNING:
        state, clock = _play_one_duel(state, rng, clock)
        duels += 1
        assert duels <= state.board.cell_count, "a match must not outrun its category supply"

    assert state.status is MatchStatus.FINISHED
    assert state.winner is not None


@pytest.mark.parametrize("seed", range(25))
def test_a_match_always_terminates_within_cells_minus_one_duels(seed: int) -> None:
    rng = random.Random(seed)
    state, _ = build_running_state(4)
    limit = state.board.cell_count - 1

    clock = 0.0
    duels = 0
    while state.status is MatchStatus.RUNNING:
        state, clock = _play_one_duel(state, rng, clock)
        duels += 1

    assert duels <= limit
    assert state.winner is not None
    assert {g.owner for g in state.groups.values()} == {state.winner}


@pytest.mark.parametrize("player_count", [2, 3, 4])
def test_every_supported_player_count_terminates(player_count: int) -> None:
    rng = random.Random(player_count)
    state, _ = build_running_state(player_count)
    clock = 0.0
    while state.status is MatchStatus.RUNNING:
        state, clock = _play_one_duel(state, rng, clock)
    assert state.winner is not None
    assert len(state.groups) + len(state.played_categories) == state.board.cell_count


def test_groups_and_unplayed_categories_fall_in_lockstep() -> None:
    rng = random.Random(99)
    state, _ = build_running_state(4)
    total = state.board.cell_count

    clock = 0.0
    while state.status is MatchStatus.RUNNING:
        played = len(state.played_categories)
        assert len(state.groups) + played == total
        state, clock = _play_one_duel(state, rng, clock)
    assert len(state.groups) + len(state.played_categories) == total


# --- Negative tests -------------------------------------------------------
#
# check_invariants is the oracle for every property test above, and a silent
# oracle proves nothing: with no test that makes it fire, each of its
# assertions can be deleted with the whole suite still green. Each test below
# hands it a state that violates exactly one invariant, chosen so no *other*
# assertion in the function would catch the same state.


def _two_groups(state: MatchState, first: Cell, second: Cell) -> tuple[Group, Group]:
    return group_containing(state, first), group_containing(state, second)


def _with_groups(state: MatchState, *changed: Group) -> MatchState:
    groups = dict(state.groups)
    for group in changed:
        groups[group.id] = group
    return replace(state, groups=groups)


def test_a_cell_claimed_by_two_groups_is_caught() -> None:
    """Overlap without a hole: the covered list grows, the covered set does not."""
    state, _ = build_running_state(4)
    left, right = _two_groups(state, Cell(0, 0), Cell(1, 0))
    broken = _with_groups(state, replace(left, cells=left.cells | right.cells))
    with pytest.raises(AssertionError, match="partition broken"):
        check_invariants(broken)


def test_a_cell_dropped_from_every_group_is_caught() -> None:
    """A hole balanced by an overlap: the count matches, the set does not."""
    state, _ = build_running_state(4)
    left, right = _two_groups(state, Cell(0, 0), Cell(1, 0))
    broken = _with_groups(state, replace(left, cells=right.cells))
    with pytest.raises(AssertionError, match="partition broken"):
        check_invariants(broken)


def test_a_group_of_two_non_adjacent_cells_is_caught() -> None:
    """The far cell is moved, not copied, so the partition still holds exactly."""
    state, _ = build_running_state(4)
    near, far = _two_groups(state, Cell(0, 0), Cell(3, 5))
    broken = _with_groups(
        state,
        replace(near, cells=near.cells | far.cells),
        replace(far, cells=frozenset()),
    )
    with pytest.raises(AssertionError, match="not orthogonally connected"):
        check_invariants(broken)


def test_two_groups_sharing_a_category_is_caught() -> None:
    state, _ = build_running_state(4)
    left, right = _two_groups(state, Cell(0, 0), Cell(1, 0))
    broken = _with_groups(state, replace(right, category=left.category))
    with pytest.raises(AssertionError, match="share a category"):
        check_invariants(broken)


def test_a_played_category_still_on_the_board_is_caught() -> None:
    """Swapping in an already-played category keeps every count in lockstep."""
    state, _ = build_running_state(4)
    state, _ = _play_one_duel(state, random.Random(7), 0.0)
    burned = next(iter(state.played_categories))
    victim = next(iter(state.groups.values()))
    broken = _with_groups(state, replace(victim, category=burned))
    with pytest.raises(AssertionError, match="played category is still on the board"):
        check_invariants(broken)


def test_groups_falling_out_of_lockstep_is_caught() -> None:
    """Two adjacent groups merged with no category burned: 23 groups, 0 played."""
    state, _ = build_running_state(4)
    left, right = _two_groups(state, Cell(0, 0), Cell(1, 0))
    groups = {gid: g for gid, g in state.groups.items() if gid != right.id}
    groups[left.id] = replace(left, cells=left.cells | right.cells)
    with pytest.raises(AssertionError, match="lockstep"):
        check_invariants(replace(state, groups=groups))
