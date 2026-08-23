from datetime import timedelta

import pytest

from budge.domain.actions import StartDuel
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.state import DuelPhase
from budge.domain.timing import deadline_of, elapsed_ms, is_expired

from .conftest import BASE_TIME, apply, at, build_declared_state, build_duel_state


def test_starting_a_duel_sets_the_anchor_and_runs() -> None:
    state, _, _, _ = build_declared_state()
    state = apply(state, StartDuel(), now=BASE_TIME)
    duel = state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.RUNNING
    assert duel.anchor == BASE_TIME
    assert duel.paused is False


def test_deadline_is_anchor_plus_the_answerer_remaining() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    expected = BASE_TIME + timedelta(milliseconds=duel.budgets.get(duel.answering))
    assert deadline_of(duel) == expected


def test_a_paused_duel_has_no_deadline() -> None:
    from dataclasses import replace

    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    assert deadline_of(replace(duel, anchor=None)) is None


def test_elapsed_is_clamped_at_both_ends() -> None:
    assert elapsed_ms(BASE_TIME, at(4.2), 60_000) == 4_200
    assert elapsed_ms(BASE_TIME, at(-5), 60_000) == 0, "a backwards clock must not add time"
    assert elapsed_ms(BASE_TIME, at(90), 60_000) == 60_000
    assert elapsed_ms(None, at(10), 60_000) == 0


def test_is_expired_only_once_the_budget_is_spent() -> None:
    state, _, _, _ = build_duel_state()
    duel = state.duel
    assert duel is not None
    budget_s = duel.budgets.get(duel.answering) / 1000
    assert is_expired(duel, at(budget_s - 0.001)) is False
    assert is_expired(duel, at(budget_s)) is True


def test_starting_a_duel_twice_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartDuel())
    assert excinfo.value.reason is RejectionReason.DUEL_NOT_DECLARED


def test_starting_a_duel_without_declaring_is_rejected() -> None:
    from .conftest import build_running_state

    state, _ = build_running_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartDuel())
    assert excinfo.value.reason is RejectionReason.NO_DUEL
