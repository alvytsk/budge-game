from dataclasses import replace

from podvinsya.domain.actions import JudgeCorrect, JudgePass
from podvinsya.domain.budgets import Budgets

from .conftest import apply, at, build_duel_state


def test_pass_keeps_the_answerer_and_advances_the_image() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None

    state = apply(state, JudgePass(), now=at(2))
    duel = state.duel
    assert duel is not None
    assert duel.answering == before.answering
    assert duel.index == 1
    assert duel.anchor == at(2)


def test_pass_charges_elapsed_time_plus_the_penalty() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, JudgePass(), now=at(2))
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.answering) == start - 2_000 - 3_000


def test_pass_that_zeroes_the_timer_loses_the_duel_immediately() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    loser = duel.answering
    thin = replace(duel, budgets=Budgets.of({duel.attacker: 1_000, duel.defender: 60_000}))
    state = replace(state, duel=thin)

    state = apply(state, JudgePass(), now=at(0.2))

    assert state.duel is None, "a duel that ended must be cleared"
    surviving = state.groups[thin.attacking_group]
    assert surviving.owner != loser


def test_pass_is_never_illegal_even_with_less_than_the_penalty_left() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    thin = replace(duel, budgets=Budgets.of({duel.attacker: 500, duel.defender: 60_000}))
    state = replace(state, duel=thin)

    state = apply(state, JudgePass(), now=at(0))

    assert state.duel is None


def test_duel_resolution_merges_groups_under_the_attacker_category_when_attacker_loses() -> (
    None
):
    """The attacker's group survives regardless of who lost the duel.

    Here the attacker is the one passing to zero, so ownership must flip to
    the defender while the surviving group keeps the attacker's category,
    revealed flag and id, and the defender's category is burned.
    """
    state, _, attacking_id, defending_id = build_duel_state()
    duel = state.duel
    assert duel is not None
    attacking_before = state.groups[attacking_id]
    defending_before = state.groups[defending_id]
    before_group_count = len(state.groups)
    before_played = state.played_categories
    before_turn_index = state.turn_index
    before_round_no = state.round_no
    thin = replace(duel, budgets=Budgets.of({duel.attacker: 1_000, duel.defender: 60_000}))
    state = replace(state, duel=thin)

    state = apply(state, JudgePass(), now=at(0.2))

    assert state.duel is None
    assert defending_id not in state.groups
    surviving = state.groups[attacking_id]
    assert surviving.id == attacking_id
    assert surviving.owner == duel.defender
    assert surviving.category == attacking_before.category
    assert surviving.revealed == attacking_before.revealed
    assert surviving.cells == attacking_before.cells | defending_before.cells
    assert len(state.groups) == before_group_count - 1
    assert state.played_categories == before_played | {defending_before.category}
    assert (state.turn_index, state.round_no) == (before_turn_index + 1, before_round_no), (
        "the turn must advance inside DuelResolved; nothing else in the suite pins this"
    )


def test_duel_resolution_merges_groups_under_the_attacker_category_when_attacker_wins() -> (
    None
):
    """Symmetric case: the defender passes to zero, so the attacker wins.

    The attacking group keeps its own owner (unchanged), its own category and
    revealed flag, absorbs the defender's cells, and the defender's category
    is burned — same merge rule, mirrored outcome.
    """
    state, _, attacking_id, defending_id = build_duel_state()
    state = apply(state, JudgeCorrect(), now=at(1))
    duel = state.duel
    assert duel is not None
    assert duel.answering == duel.defender

    attacking_before = state.groups[attacking_id]
    defending_before = state.groups[defending_id]
    before_group_count = len(state.groups)
    before_played = state.played_categories
    before_turn_index = state.turn_index
    before_round_no = state.round_no
    thin = replace(duel, budgets=Budgets.of({duel.attacker: 60_000, duel.defender: 1_000}))
    state = replace(state, duel=thin)

    state = apply(state, JudgePass(), now=at(1.2))

    assert state.duel is None
    assert defending_id not in state.groups
    surviving = state.groups[attacking_id]
    assert surviving.id == attacking_id
    assert surviving.owner == duel.attacker
    assert surviving.category == attacking_before.category
    assert surviving.revealed == attacking_before.revealed
    assert surviving.cells == attacking_before.cells | defending_before.cells
    assert len(state.groups) == before_group_count - 1
    assert state.played_categories == before_played | {defending_before.category}
    assert (state.turn_index, state.round_no) == (before_turn_index + 1, before_round_no), (
        "the turn must advance inside DuelResolved; nothing else in the suite pins this"
    )
