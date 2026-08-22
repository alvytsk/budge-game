import pytest

from podvinsya.domain.actions import JudgeCorrect
from podvinsya.domain.errors import Rejected, RejectionReason

from .conftest import BASE_TIME, apply, at, build_declared_state, build_duel_state


def test_correct_answer_passes_the_turn_and_advances_the_image() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None

    state = apply(state, JudgeCorrect(), now=at(4.2))
    duel = state.duel
    assert duel is not None
    assert duel.answering == before.defender
    assert duel.index == 1
    assert duel.anchor == at(4.2)


def test_only_the_answering_player_is_charged() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    attacker_budget = before.budgets.get(before.attacker)
    defender_budget = before.budgets.get(before.defender)

    state = apply(state, JudgeCorrect(), now=at(4.2))
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.attacker) == attacker_budget - 4_200
    assert duel.budgets.get(before.defender) == defender_budget


def test_the_clock_only_runs_for_whoever_is_answering() -> None:
    state, _, _, _ = build_duel_state()
    first = state.duel
    assert first is not None

    state = apply(state, JudgeCorrect(), now=at(10))
    state = apply(state, JudgeCorrect(), now=at(13))
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(first.attacker) == first.budgets.get(first.attacker) - 10_000
    assert duel.budgets.get(first.defender) == first.budgets.get(first.defender) - 3_000
    assert duel.answering == first.attacker
    assert duel.index == 2


def test_judging_a_declared_but_unstarted_duel_is_rejected() -> None:
    state, _, _, _ = build_declared_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=BASE_TIME)
    assert excinfo.value.reason is RejectionReason.DUEL_NOT_RUNNING


def test_judging_without_a_duel_is_rejected() -> None:
    from .conftest import build_running_state

    state, _ = build_running_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=BASE_TIME)
    assert excinfo.value.reason is RejectionReason.NO_DUEL
