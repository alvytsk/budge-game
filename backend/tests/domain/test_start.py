from dataclasses import replace

import pytest

from budge.domain.actions import StartMatch
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.rules import next_turn
from budge.domain.state import MatchStatus

from .conftest import apply, build_dealt_state, build_setup_state


def test_start_switches_status_and_fixes_turn_order() -> None:
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    assert state.status is MatchStatus.RUNNING
    assert state.turn_order == players
    assert state.turn_index == 0
    assert state.round_no == 1
    assert state.current_player() == players[0]


def test_start_before_dealing_is_rejected() -> None:
    state, _ = build_setup_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartMatch())
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_start_twice_is_rejected() -> None:
    state, _ = build_dealt_state(4)
    state = apply(state, StartMatch())
    with pytest.raises(Rejected) as excinfo:
        apply(state, StartMatch())
    assert excinfo.value.reason is RejectionReason.WRONG_STATUS


def test_next_turn_advances_and_wraps_the_round() -> None:
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    index, round_no = next_turn(state)
    assert (index, round_no) == (1, 1)

    at_last = replace(state, turn_index=3)
    assert next_turn(at_last) == (0, 2)


def test_next_turn_skips_eliminated_players() -> None:
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    knocked_out = tuple(
        replace(p, eliminated=(p.id == players[1])) for p in state.players
    )
    state = replace(state, players=knocked_out)
    assert next_turn(state) == (2, 1)


def test_next_turn_falls_back_when_every_player_is_eliminated() -> None:
    state, _ = build_dealt_state(4)
    state = apply(state, StartMatch())

    # The cursor is deliberately moved off (0, 1) first. Left at its starting value,
    # this assertion could not tell "returned the cursor unchanged" apart from a
    # fallback that returned hardcoded zeros — both would be (0, 1).
    wiped = replace(
        state,
        turn_index=2,
        round_no=7,
        players=tuple(replace(p, eliminated=True) for p in state.players),
    )

    # The bounded loop finds nobody and falls through. This must return the cursor
    # unchanged — neither spinning forever nor raising. Nothing else pins that.
    assert next_turn(wiped) == (2, 7)
