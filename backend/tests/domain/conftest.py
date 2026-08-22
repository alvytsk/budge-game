from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from podvinsya.domain.actions import AddPlayer, AssignSecret, Command, CreateMatch, DealBoard
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DealPlan, DealtCell, DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, GroupId, MatchId, PlayerId
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


def make_deal(
    board: BoardSize,
    players: tuple[PlayerId, ...],
    secrets: dict[PlayerId, CategoryId],
) -> DealPlan:
    """Deterministic deal on a Latin-square pattern, secret on each owner's first cell.

    Owner is (col + row) % n, so no two orthogonally adjacent cells share an
    owner and every neighbour is a legal target from move one. A plain
    round-robin over the row-major cell order would hand each player a solid
    column whenever the board width is a multiple of the player count, and
    then "the cell below is a legal target" stops being true.

    Even counts hold for the three default boards in BOARDS; the assert at the
    end makes any other board loud rather than silently lopsided.
    """
    n = len(players)
    cells = board.cells()
    per_player = len(cells) // n
    seen: set[PlayerId] = set()
    dealt: list[DealtCell] = []
    for cell in cells:
        owner = players[(cell.col + cell.row) % n]
        is_first_for_owner = owner not in seen
        seen.add(owner)
        category = secrets[owner] if is_first_for_owner else CategoryId(uuid4())
        dealt.append(
            DealtCell(
                cell=cell,
                owner=owner,
                category=category,
                group_id=GroupId(uuid4()),
                revealed=not is_first_for_owner,
            )
        )
    counts = Counter(d.owner for d in dealt)
    assert set(counts.values()) == {per_player}, f"uneven deal: {counts}"
    return DealPlan(cells=tuple(dealt))


def build_dealt_state(player_count: int = 4) -> tuple[MatchState, tuple[PlayerId, ...]]:
    state, players = build_setup_state(player_count)
    deal = make_deal(state.board, players, dict(state.secrets))
    return apply(state, DealBoard(), deal=deal), players


@pytest.fixture
def dealt_state() -> tuple[MatchState, tuple[PlayerId, ...]]:
    return build_dealt_state(4)
