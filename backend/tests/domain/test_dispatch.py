from datetime import UTC, datetime
from uuid import uuid4

import pytest

from podvinsya.domain.actions import CreateMatch
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.evolve import evolve, fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchStatus

NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def test_create_match_emits_match_created() -> None:
    match_id = MatchId(uuid4())
    board = BoardSize(width=4, height=6)
    state = create_initial_state(match_id, board, MatchSettings())

    events = decide(state, CreateMatch(board=board, settings=MatchSettings(), player_count=4), DecisionContext(now=NOW))

    assert events == (MatchCreated(board=board, settings=MatchSettings(), player_count=4),)


def test_evolve_increments_seq_for_every_event() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    evolved = evolve(state, MatchCreated(board=BoardSize(4, 6), settings=MatchSettings(), player_count=4))
    assert evolved.seq == 1
    assert evolved.status is MatchStatus.SETUP
    assert evolved.player_count == 4


def test_fold_applies_events_in_order() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    folded = fold(state, [MatchCreated(board=BoardSize(4, 6), settings=MatchSettings(), player_count=4)])
    assert folded.seq == 1


def test_decide_is_pure() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    command = CreateMatch(board=BoardSize(4, 6), settings=MatchSettings(), player_count=4)
    ctx = DecisionContext(now=NOW)
    assert decide(state, command, ctx) == decide(state, command, ctx)


def test_unknown_event_is_a_type_error_not_a_silent_noop() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(4, 6), MatchSettings())
    with pytest.raises(NotImplementedError):
        evolve(state, object())  # type: ignore[arg-type]
