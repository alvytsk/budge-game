"""Legal event streams, produced by driving the domain rather than by hand.

Everything downstream of the domain — the codec, the event store, the
projection, recovery — needs events that are exactly what `decide` emits.
Literals typed into a test drift from the real thing the moment a field is
added, and the drift is invisible: the test still passes, it just stops
describing the system.

`make_deal` lives here rather than in `tests/domain/conftest.py` because a
conftest is not importable from another test directory. The domain suite
imports it from here.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID, uuid4

from podvinsya.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    CreateMatch,
    DealBoard,
    DeclareAttack,
    ExpireTimer,
    JudgeCorrect,
    JudgePass,
    PauseDuel,
    ResumeDuel,
    StartDuel,
    StartMatch,
    UndoLastJudgement,
)
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DealPlan, DealtCell, DecisionContext, JournalEntry
from podvinsya.domain.decide import decide
from podvinsya.domain.events import Event
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, MatchId, PlayerId
from podvinsya.domain.rules import legal_targets
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState, MatchStatus

BASE_TIME = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
COLOURS = ("#e5484d", "#3b82f6", "#22c55e", "#a855f7")

# Enough for one duel with room to undo and replay. The domain rejects with
# IMAGES_EXHAUSTED past the end of the pack, which no stream here reaches.
IMAGES_PER_DUEL = 24


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


def deterministic_uuid4(start: int = 1) -> Callable[[], UUID]:
    """A drop-in replacement for this module's `uuid4`, yielding `UUID(int=n)`
    for n = start, start + 1, ...

    Every id `build_rich_stream` mints goes through the bare `uuid4()` name
    in *this* module — nothing in `podvinsya.domain` calls it — so
    `monkeypatch.setattr(streams, "uuid4", deterministic_uuid4())` makes the
    whole stream reproducible, values included, without touching the domain
    and without changing any other test's ids.

    Only a test that needs to compare exact values across separate
    invocations of `build_rich_stream` should reach for this — the codec's
    golden-file test is the first one. Every other consumer wants fresh
    random ids: later tasks build two streams in one test and write both to
    a database whose primary keys would collide if the ids repeated.
    """
    counter = count(start)

    def _uuid4() -> UUID:
        return UUID(int=next(counter))

    return _uuid4


@dataclass(frozen=True, slots=True)
class Recorded:
    """A folded state and the exact events that produced it."""

    state: MatchState
    events: tuple[Event, ...]


class _Recorder:
    """Drives commands through decide/evolve and keeps every event.

    It also assembles `duel_journal`, which is the runtime's job in
    production: the domain reads the journal but never builds it, and the
    rule that undo cannot cross into the previous duel lives entirely in
    whoever assembles it. Here that is the `journal.clear()` call at the end
    of `_declare_and_start`, once a new duel has started.
    """

    def __init__(self, match_id: MatchId, board: BoardSize, settings: MatchSettings) -> None:
        self.state = create_initial_state(match_id, board, settings)
        self.events: list[Event] = []
        self.journal: list[JournalEntry] = []

    def apply(self, command: Command, *, now: datetime, **ctx_kwargs: object) -> None:
        ctx = DecisionContext(now=now, **ctx_kwargs)  # type: ignore[arg-type]
        produced = decide(self.state, command, ctx)
        self.events.extend(produced)
        self.state = fold(self.state, produced)

    def snapshot(self) -> None:
        """Record the duel as it stands immediately before a judging event."""
        duel = self.state.duel
        assert duel is not None
        self.journal.append(
            JournalEntry(
                seq=self.state.seq,
                budgets=duel.budgets,
                answering=duel.answering,
                image_index=duel.index,
            )
        )


def _declare_and_start(recorder: _Recorder, now: datetime) -> None:
    """Attack with the current player's first group that has a legal target.

    One always exists while the match is running: the board is connected and
    the current player owns some but not all of it, so some cell of theirs
    borders a cell of someone else's.
    """
    attacker = recorder.state.current_player()
    attacking = next(
        group
        for group in recorder.state.groups_of(attacker)
        if legal_targets(recorder.state, group.id)
    )
    defending = sorted(legal_targets(recorder.state, attacking.id))[0]
    recorder.apply(
        DeclareAttack(attacking_group=attacking.id, defending_group=defending),
        now=now,
        image_order=tuple(ImageId(uuid4()) for _ in range(IMAGES_PER_DUEL)),
    )
    recorder.apply(StartDuel(), now=now)
    recorder.journal.clear()


def _expire(recorder: _Recorder, now: datetime) -> datetime:
    """Let the answering side's clock run out, which resolves the duel.

    The deadline is derived, never stored: anchor plus the answering
    player's remaining time (§4.1). A second past it is unambiguously late.
    """
    duel = recorder.state.duel
    assert duel is not None and duel.anchor is not None
    deadline = duel.anchor + timedelta(milliseconds=duel.budgets.get(duel.answering))
    now = deadline + timedelta(seconds=1)
    recorder.apply(ExpireTimer(deadline_id=0), now=now)
    return now


def build_rich_stream() -> Recorded:
    """One complete match containing every event type at least once.

    Two players on the smallest legal board: twelve cells, so the group
    count -- one group per cell to start -- can merge at most eleven times
    before the whole board is a single group; that bound is what makes the
    loop below provably terminate. In practice the match ends sooner:
    `MatchWon` fires the moment one player holds zero groups, which happens
    before every group has merged into one. As played here that is ten
    duels and forty-four events, the same every run (see
    test_the_stream_is_deterministic_in_shape). The first duel is played by
    hand so that judging, passing, pausing, resuming and undoing all appear;
    the remaining nine are decided by the clock, the shortest legal way to
    finish the rest of the match.
    """
    board = BoardSize(3, 4)
    settings = MatchSettings()
    recorder = _Recorder(MatchId(uuid4()), board, settings)
    players = (PlayerId(uuid4()), PlayerId(uuid4()))
    now = BASE_TIME

    recorder.apply(CreateMatch(board=board, settings=settings, player_count=2), now=now)
    for index, player_id in enumerate(players):
        recorder.apply(
            AddPlayer(player_id=player_id, name=f"Игрок {index + 1}", colour=COLOURS[index]),
            now=now,
        )
        recorder.apply(AssignSecret(player_id=player_id, category=CategoryId(uuid4())), now=now)
    recorder.apply(
        DealBoard(), now=now, deal=make_deal(board, players, dict(recorder.state.secrets))
    )
    recorder.apply(StartMatch(), now=now)

    _declare_and_start(recorder, now)
    now += timedelta(seconds=4)
    recorder.snapshot()
    recorder.apply(JudgeCorrect(), now=now)
    now += timedelta(seconds=3)
    recorder.snapshot()
    recorder.apply(JudgePass(), now=now)
    now += timedelta(seconds=2)
    recorder.apply(PauseDuel(), now=now)
    now += timedelta(seconds=30)  # the room waits; a pause charges nobody
    recorder.apply(ResumeDuel(), now=now)
    now += timedelta(seconds=1)
    recorder.apply(UndoLastJudgement(), now=now, duel_journal=tuple(recorder.journal))
    recorder.journal.pop()
    now = _expire(recorder, now)

    while recorder.state.status is not MatchStatus.FINISHED:
        _declare_and_start(recorder, now)
        now = _expire(recorder, now)

    return Recorded(state=recorder.state, events=tuple(recorder.events))
