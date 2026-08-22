from dataclasses import replace

from podvinsya.domain.actions import ExpireTimer, JudgeCorrect
from podvinsya.domain.board import is_connected

from .conftest import apply, at, build_duel_state


def _force_loss(state, loser):  # type: ignore[no-untyped-def]
    """Wind the answering player's clock down so the next ExpireTimer resolves."""
    duel = state.duel
    assert duel is not None
    aimed = replace(duel, answering=loser, budgets=duel.budgets.with_value(loser, 1_000))
    return replace(state, duel=aimed)


def test_attacker_wins_and_takes_the_defending_group() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    attacker, defender = duel.attacker, duel.defender
    kept_category = state.groups[attacking].category
    burned_category = state.groups[defending].category
    cells_before = state.groups[attacking].cells | state.groups[defending].cells

    state = apply(_force_loss(state, defender), ExpireTimer(deadline_id=0), now=at(1))

    assert state.duel is None
    assert defending not in state.groups
    merged = state.groups[attacking]
    assert merged.owner == attacker
    assert merged.cells == cells_before
    assert merged.category == kept_category
    assert burned_category in state.played_categories


def test_attacker_loses_and_the_defender_takes_the_attacking_group() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    attacker, defender = duel.attacker, duel.defender
    kept_category = state.groups[attacking].category
    cells_before = state.groups[attacking].cells | state.groups[defending].cells

    state = apply(_force_loss(state, attacker), ExpireTimer(deadline_id=0), now=at(1))

    merged = state.groups[attacking]
    assert merged.owner == defender, "the winner takes both groups"
    assert merged.cells == cells_before
    assert merged.category == kept_category, "the attacker category survives regardless of who won"


def test_the_merged_group_keeps_the_attacker_revealed_flag() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    before = state.groups[attacking].revealed
    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))
    assert state.groups[attacking].revealed is before


def test_the_merged_group_is_connected() -> None:
    state, _, attacking, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))
    assert is_connected(state.groups[attacking].cells)


def test_every_duel_costs_exactly_one_group_and_one_category() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    groups_before = len(state.groups)
    played_before = len(state.played_categories)

    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))

    assert len(state.groups) == groups_before - 1
    assert len(state.played_categories) == played_before + 1


def test_turn_advances_after_a_duel() -> None:
    state, players, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    before = state.current_player()
    state = apply(_force_loss(state, duel.defender), ExpireTimer(deadline_id=0), now=at(1))
    assert state.current_player() != before


def test_a_judging_command_arriving_after_the_deadline_resolves_as_expiry() -> None:
    state, _, attacking, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    late = duel.budgets.get(duel.answering) / 1000 + 5

    state = apply(state, JudgeCorrect(), now=at(late))

    assert state.duel is None, "the clock is authoritative over a late command"
    assert state.groups[attacking].owner == duel.defender


def test_expire_timer_without_a_duel_is_ignored() -> None:
    from .conftest import build_running_state
    from podvinsya.domain.context import DecisionContext
    from podvinsya.domain.decide import decide
    from .conftest import BASE_TIME

    state, _ = build_running_state(4)
    assert decide(state, ExpireTimer(deadline_id=7), DecisionContext(now=BASE_TIME)) == ()
