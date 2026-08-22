from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from podvinsya.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    CreateMatch,
    DealBoard,
    DeclareAttack,
    StartDuel,
    StartMatch,
)
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, MatchId, PlayerId
from podvinsya.domain.rules import legal_targets
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState
from support.streams import make_deal as make_deal

BASE_TIME = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
COLOURS = ("#e5484d", "#3b82f6", "#22c55e", "#a855f7")
BOARDS = {2: BoardSize(3, 4), 3: BoardSize(3, 6), 4: BoardSize(4, 6)}
IMAGE_POOL: tuple[ImageId, ...] = tuple(ImageId(uuid4()) for _ in range(40))


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


def build_dealt_state(player_count: int = 4) -> tuple[MatchState, tuple[PlayerId, ...]]:
    state, players = build_setup_state(player_count)
    deal = make_deal(state.board, players, dict(state.secrets))
    return apply(state, DealBoard(), deal=deal), players


@pytest.fixture
def dealt_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_dealt_state(4)


def build_running_state(player_count: int = 4) -> tuple[MatchState, tuple[PlayerId, ...]]:
    state, players = build_dealt_state(player_count)
    return apply(state, StartMatch()), players


@pytest.fixture
def running_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_running_state(4)


def build_declared_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    state, players = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(
        g
        for g in state.groups.values()
        if g.owner == attacker_id and legal_targets(state, g.id)
    )
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    state = apply(
        state,
        DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
        image_order=IMAGE_POOL,
    )
    return state, players, attacking.id, defending_id


@pytest.fixture
def declared_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    return build_declared_state()


def build_duel_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    state, players, attacking, defending = build_declared_state()
    return apply(state, StartDuel(), now=BASE_TIME), players, attacking, defending


@pytest.fixture
def duel_state() -> tuple[MatchState, tuple[PlayerId, ...], GroupId, GroupId]:
    return build_duel_state()
