from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from podvinsya.domain.actions import AddPlayer, AssignSecret, Command, CreateMatch
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, MatchId, PlayerId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState

BASE_TIME = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
COLOURS = ("#e5484d", "#3b82f6", "#22c55e", "#a855f7")
BOARDS = {2: BoardSize(3, 4), 3: BoardSize(3, 6), 4: BoardSize(4, 6)}


def at(seconds: float) -> datetime:
    return BASE_TIME + timedelta(seconds=seconds)


def apply(
    state: MatchState,
    command: Command,
    *,
    now: datetime = BASE_TIME,
    **ctx_kwargs: object,
) -> MatchState:
    ctx = DecisionContext(now=now, **ctx_kwargs)  # type: ignore[arg-type]
    return fold(state, decide(state, command, ctx))


@pytest.fixture
def created_state() -> MatchState:
    state = create_initial_state(MatchId(uuid4()), BOARDS[4], MatchSettings())
    return apply(state, CreateMatch(board=BOARDS[4], settings=MatchSettings(), player_count=4))


def build_setup_state(
    player_count: int = 4,
) -> tuple[MatchState, tuple[PlayerId, ...]]:
    """A match with all players added and all secrets assigned, built through decide/evolve."""
    board = BOARDS[player_count]
    state = create_initial_state(MatchId(uuid4()), board, MatchSettings())
    state = apply(
        state,
        CreateMatch(board=board, settings=MatchSettings(), player_count=player_count),
    )
    players = tuple(PlayerId(uuid4()) for _ in range(player_count))
    for index, player_id in enumerate(players):
        state = apply(
            state,
            AddPlayer(player_id=player_id, name=f"P{index + 1}", colour=COLOURS[index]),
        )
        state = apply(
            state,
            AssignSecret(player_id=player_id, category=CategoryId(uuid4())),
        )
    return state, players


@pytest.fixture
def setup_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_setup_state(4)
