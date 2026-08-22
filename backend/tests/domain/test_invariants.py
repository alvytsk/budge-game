import random

import pytest

from podvinsya.domain import check_invariants
from podvinsya.domain.actions import DeclareAttack, JudgeCorrect, StartDuel
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.rules import legal_targets
from podvinsya.domain.state import MatchState, MatchStatus

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
