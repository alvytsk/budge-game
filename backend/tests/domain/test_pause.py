import pytest

from budge.domain.actions import JudgeCorrect, PauseDuel, ResumeDuel
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.timing import deadline_of

from .conftest import apply, at, build_duel_state


def test_pause_freezes_the_clock_and_drops_the_deadline() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, PauseDuel(), now=at(5))
    duel = state.duel
    assert duel is not None
    assert duel.paused is True
    assert duel.anchor is None
    assert deadline_of(duel) is None
    assert duel.budgets.get(before.answering) == start - 5_000


def test_time_spent_paused_is_never_charged() -> None:
    state, _, _, _ = build_duel_state()
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, PauseDuel(), now=at(5))
    state = apply(state, ResumeDuel(), now=at(305))
    state = apply(state, JudgeCorrect(), now=at(307))

    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.answering) == start - 7_000, (
        "five minutes of pause must cost nothing"
    )


def test_resume_reanchors_to_now() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))
    state = apply(state, ResumeDuel(), now=at(60))
    duel = state.duel
    assert duel is not None
    assert duel.anchor == at(60)
    assert duel.paused is False


def test_judging_while_paused_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))
    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=at(6))
    assert excinfo.value.reason is RejectionReason.DUEL_PAUSED


def test_pausing_twice_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    state = apply(state, PauseDuel(), now=at(5))
    with pytest.raises(Rejected) as excinfo:
        apply(state, PauseDuel(), now=at(6))
    assert excinfo.value.reason is RejectionReason.DUEL_PAUSED


def test_resuming_a_running_duel_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, ResumeDuel(), now=at(5))
    assert excinfo.value.reason is RejectionReason.DUEL_NOT_PAUSED
