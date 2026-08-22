"""§6.2's loop, the twenty lines everything else in this plan exists to
serve, plus §6.3's runtime-health line: a broadcaster failure or a content
shortfall never quarantines, everything else does.

Most tests here drive `MatchRuntime._consume` directly rather than through
`submit`/`run`: it is the method the design's own pseudocode names, and
calling it directly keeps each test to the one behaviour it is about
without a background consumer task to create and clean up. A few genuinely
need the real queue-plus-consumer -- ordering across several commands, and
a deadline firing back into the queue the realistic way -- and those spin
up `run()` as a task via `_consuming` and tear it down themselves.

No test here touches a database, so none of them carry
`pytest.mark.integration`.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from random import Random
from uuid import uuid4

import pytest

from podvinsya.db.repository import LoadedMatch
from podvinsya.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    CreateMatch,
    DealBoard,
    ExpireTimer,
    StartMatch,
)
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.errors import RejectionReason
from podvinsya.domain.events import DuelStarted, Event, MatchCreated
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, MatchId, PlayerId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState, MatchStatus, Player
from podvinsya.domain.timing import deadline_of
from podvinsya.runtime import match as match_module
from podvinsya.runtime.commit import CommitPath
from podvinsya.runtime.errors import Quarantined
from podvinsya.runtime.materialiser import Materialiser
from podvinsya.runtime.match import MatchRuntime
from podvinsya.runtime.origins import (
    Accepted,
    CommandOutcome,
    Failed,
    FutureOrigin,
    NoOp,
    QueuedCommand,
    Rejected,
    SystemOrigin,
)
from podvinsya.runtime.scheduler import DeadlineScheduler
from podvinsya.services.ports import Reconciliation, RuntimeCode, Transaction
from support.fakes import BreakingBroadcaster, FakeCategoryBank, FakeClock, RecordingBroadcaster
from support.streams import BASE_TIME, Recorded, build_rich_stream

_BOARD = BoardSize(3, 4)
_SETTINGS = MatchSettings()
_PLAYER_COUNT = 2


# --------------------------------------------------------------------------
# Shared doubles -- deliberately minimal, local copies rather than an import
# from test_commit.py's private classes, matching that module's own
# precedent of not sharing fakes across test files.
# --------------------------------------------------------------------------


class _RecordingTransaction:
    """Appends never fail; every call is recorded in call order.

    `yield_on_append` inserts a genuine `await` inside the transaction body
    -- exactly the kind of suspension point a real database round-trip
    would have -- so a consumer loop that processes commands concurrently
    instead of one at a time has a real chance to interleave and get
    caught, rather than happening to look sequential by luck.
    """

    def __init__(
        self, sink: list[tuple[MatchId, int, tuple[Event, ...], str]], *, yield_on_append: bool
    ) -> None:
        self._sink = sink
        self._yield_on_append = yield_on_append

    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        if self._yield_on_append:
            await asyncio.sleep(0)
        self._sink.append((match_id, expected_last_seq, tuple(events), operation_id))


class _RecordingUoW:
    """Nothing ever fails. `committed` records what got appended."""

    def __init__(self, *, yield_on_append: bool = False) -> None:
        self.committed: list[tuple[MatchId, int, tuple[Event, ...], str]] = []
        self._yield_on_append = yield_on_append

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Transaction]:
        yield _RecordingTransaction(self.committed, yield_on_append=self._yield_on_append)

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        raise AssertionError("reconcile must not be called when the commit never raised")


class _NullRepository:
    """No test in this module reads the repository -- none exercises
    `UndoLastJudgement`, the only command that does."""

    async def create(self, match_id: MatchId, event: MatchCreated, *, operation_id: str) -> None:
        raise NotImplementedError("no test in this module reads the repository")

    async def read_events(self, match_id: MatchId) -> tuple[Event, ...]:
        raise NotImplementedError("no test in this module reads the repository")

    async def load(self, match_id: MatchId) -> LoadedMatch:
        raise NotImplementedError("no test in this module reads the repository")


class _RaisingMaterialiser(Materialiser):
    """Every `build` call raises, unconditionally -- the shortest path to
    a `Failed(RuntimeCode.INTERNAL, ...)` outcome from `CommitPath`."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock, _NullRepository(), FakeCategoryBank(), Random(0))

    async def build(
        self, state: MatchState, command: Command, tx: Transaction, *, at: datetime | None = None
    ) -> DecisionContext:
        raise RuntimeError("materialiser blew up")


class _CountingMaterialiser(Materialiser):
    """`calls` counts real `build` invocations -- proof that a stale
    `ExpireTimer` never reaches the materialiser, let alone `decide`."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock, _NullRepository(), FakeCategoryBank(), Random(0))
        self.calls = 0

    async def build(
        self, state: MatchState, command: Command, tx: Transaction, *, at: datetime | None = None
    ) -> DecisionContext:
        self.calls += 1
        return await super().build(state, command, tx, at=at)


async def _noop_fire(deadline_id: int) -> None:
    return None


def _inert_scheduler(clock: FakeClock) -> DeadlineScheduler:
    """A scheduler whose `fire` is never expected to run within the test's
    lifetime -- used wherever a test needs *a* scheduler but its firing is
    not the point."""
    return DeadlineScheduler(clock, _noop_fire)


def _materialiser(clock: FakeClock) -> Materialiser:
    return Materialiser(clock, _NullRepository(), FakeCategoryBank(), Random(0))


def _fresh_state(
    *,
    seq: int = 0,
    status: MatchStatus = MatchStatus.SETUP,
    player_count: int = _PLAYER_COUNT,
) -> MatchState:
    return MatchState(
        id=MatchId(uuid4()),
        seq=seq,
        status=status,
        board=_BOARD,
        settings=_SETTINGS,
        player_count=player_count,
    )


def _expected_match_created() -> MatchCreated:
    return MatchCreated(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT)


def _deal_ready_state() -> MatchState:
    """Two players, each with a secret assigned -- built by actually
    running `decide`/`fold`, the same way `tests/runtime/test_commit.py`
    does, so it is guaranteed to be a state the domain itself would
    produce."""
    state = create_initial_state(MatchId(uuid4()), _BOARD, _SETTINGS)

    def apply(command: Command, **ctx_kwargs: object) -> None:
        nonlocal state
        ctx = DecisionContext(now=BASE_TIME, **ctx_kwargs)  # type: ignore[arg-type]
        state = fold(state, decide(state, command, ctx))

    apply(CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT))
    players = (PlayerId(uuid4()), PlayerId(uuid4()))
    for index, player_id in enumerate(players):
        apply(AddPlayer(player_id=player_id, name=f"Player {index}", colour="#000000"))
        apply(AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    return state


def _fold_prefix(match_id: MatchId, events: Sequence[Event]) -> MatchState:
    """Recover the state a truncated event prefix implies -- mirrors
    `tests/runtime/test_scheduler.py`'s own helper of the same name."""
    genesis = events[0]
    assert isinstance(genesis, MatchCreated)
    return fold(create_initial_state(match_id, genesis.board, genesis.settings), events)


def _state_after(recorded: Recorded, event_type: type) -> MatchState:
    """The state immediately after the first event of `event_type`."""
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type)) + 1
    return _fold_prefix(recorded.state.id, recorded.events[:cut])


@asynccontextmanager
async def _consuming(runtime: MatchRuntime) -> AsyncIterator[None]:
    """Run `runtime.run()` as a background task for the lifetime of the
    `with` block, then cancel and await it -- the only way to exercise the
    real queue-plus-consumer without leaving a task pending at test exit."""
    task = asyncio.create_task(runtime.run())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# --------------------------------------------------------------------------
# The order below the commit line
# --------------------------------------------------------------------------


async def test_a_committed_command_folds_reschedules_publishes_and_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In that order. Assert the order, not just that all four happened --
    a frame published before the deadline is armed advertises a timer the
    runtime is not yet keeping."""
    order: list[str] = []

    def _spy_fold(state: MatchState, events: Sequence[Event]) -> MatchState:
        order.append("fold")
        return fold(state, events)

    monkeypatch.setattr(match_module, "fold", _spy_fold)

    class _OrderScheduler(DeadlineScheduler):
        def reschedule(self, state: MatchState) -> None:
            order.append("reschedule")
            super().reschedule(state)

    class _OrderBroadcaster(RecordingBroadcaster):
        def publish(
            self,
            match_id: MatchId,
            base_seq: int,
            state: MatchState,
            events: Sequence[Event],
        ) -> None:
            order.append("publish")
            super().publish(match_id, base_seq, state, events)

    class _OrderOrigin:
        def __init__(self) -> None:
            self.outcome: CommandOutcome | None = None

        def resolve_ok(self, events: Sequence[Event]) -> None:
            order.append("resolve")
            self.outcome = Accepted(tuple(events))

        def resolve_noop(self) -> None:
            raise AssertionError("expected an accepted outcome")

        def resolve_rejected(self, reason: RejectionReason) -> None:
            raise AssertionError("expected an accepted outcome")

        def resolve_failed(self, code: RuntimeCode, message: str) -> None:
            raise AssertionError("expected an accepted outcome")

    state = _fresh_state()
    clock = FakeClock(BASE_TIME)
    scheduler = _OrderScheduler(clock, _noop_fire)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = _OrderBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, scheduler, broadcaster)
    origin = _OrderOrigin()
    queued = QueuedCommand.issue(
        CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT), origin
    )

    await runtime._consume(queued)

    assert order == ["fold", "reschedule", "publish", "resolve"]
    assert origin.outcome == Accepted((_expected_match_created(),))


async def test_the_published_frame_carries_the_seq_before_the_batch() -> None:
    """`base_seq` is what a subscriber uses to tell whether it missed
    something. Sending the post-fold seq would make every gap invisible."""
    state = _fresh_state(seq=0)
    clock = FakeClock(BASE_TIME)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), broadcaster)
    queued = QueuedCommand.issue(
        CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT), FutureOrigin()
    )

    await runtime._consume(queued)

    assert len(broadcaster.frames) == 1
    frame = broadcaster.frames[0]
    assert frame.base_seq == 0, "base_seq must be the seq before this batch was folded in"
    assert frame.state.seq == 1, "the state handed to the broadcaster is the post-fold state"
    assert frame.events == (_expected_match_created(),)


async def test_a_no_op_resolves_without_publishing() -> None:
    """Nothing was persisted, so there is nothing for the room to see."""
    player_id = PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = MatchState(
        id=MatchId(uuid4()),
        seq=3,
        status=MatchStatus.SETUP,
        board=_BOARD,
        settings=_SETTINGS,
        player_count=_PLAYER_COUNT,
        players=(Player(player_id, "A", "#fff"),),
        secrets={player_id: category},
    )
    clock = FakeClock(BASE_TIME)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), broadcaster)
    origin = FutureOrigin()
    queued = QueuedCommand.issue(AssignSecret(player_id=player_id, category=category), origin)

    await runtime._consume(queued)

    outcome = await origin.result()
    assert outcome == NoOp()
    assert broadcaster.frames == []
    assert runtime.state == state


async def test_a_rejection_leaves_the_state_and_the_deadline_untouched() -> None:
    class _CountingScheduler(DeadlineScheduler):
        def __init__(self, clock: FakeClock) -> None:
            super().__init__(clock, _noop_fire)
            self.reschedule_calls = 0

        def reschedule(self, state: MatchState) -> None:
            self.reschedule_calls += 1
            super().reschedule(state)

    state = _fresh_state(seq=7, status=MatchStatus.RUNNING)
    clock = FakeClock(BASE_TIME)
    scheduler = _CountingScheduler(clock)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, scheduler, broadcaster)
    origin = FutureOrigin()
    queued = QueuedCommand.issue(StartMatch(), origin)

    await runtime._consume(queued)

    outcome = await origin.result()
    assert isinstance(outcome, Rejected)
    assert outcome.reason is RejectionReason.WRONG_STATUS
    assert runtime.state == state, "a rejection must not change the in-memory state at all"
    assert broadcaster.frames == []
    assert scheduler.reschedule_calls == 0, "a rejection must never touch the deadline"


# --------------------------------------------------------------------------
# The stale-ExpireTimer identity check (§4.3)
# --------------------------------------------------------------------------


async def test_a_stale_expire_timer_is_dropped_as_a_no_op() -> None:
    """A judgement re-anchors the duel and a timer already in flight for
    the old anchor arrives afterwards. Applying it would resolve a duel
    that has seconds left."""
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None

    clock = FakeClock(BASE_TIME)
    scheduler = DeadlineScheduler(clock, _noop_fire)
    scheduler.reschedule(state)
    await clock.settle()
    current_id = scheduler.deadline_id
    assert current_id is not None

    materialiser = _CountingMaterialiser(clock)
    commit = CommitPath(_RecordingUoW(), materialiser, clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, scheduler, broadcaster)
    origin = FutureOrigin()
    queued = QueuedCommand.issue(ExpireTimer(deadline_id=current_id + 1), origin)

    await runtime._consume(queued)

    outcome = await origin.result()
    assert outcome == NoOp()
    assert materialiser.calls == 0, "decide must never see a stale timer"
    assert runtime.state == state
    assert broadcaster.frames == []

    scheduler.cancel()
    await clock.settle()


async def test_a_current_expire_timer_is_applied() -> None:
    """The other half: the identity check must not swallow the real one.

    Wired the realistic way, unlike the stale-timer test above: the
    scheduler's own `fire` submits the `ExpireTimer` back into this
    runtime's queue, exactly as a future `MatchManager` will connect the
    two, and `run()` is what actually consumes it. `fire` can only be
    defined after `runtime` exists -- the constructor takes an
    already-built scheduler, so the two are necessarily assembled in that
    order -- and it wraps `submit` in try/except: `_sleep_and_fire` awaits
    `fire` directly, so anything `fire` raises becomes an exception on the
    scheduler's own background task. asyncio only logs an unretrieved
    task exception; it never becomes a `warnings`-module warning, so
    `pytest -W error` -- which this suite otherwise leans on for pristine
    output -- would not catch a `fire` that let one escape.
    """
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None
    deadline = deadline_of(state.duel)
    assert deadline is not None

    clock = FakeClock(BASE_TIME)
    runtime_box: list[MatchRuntime] = []

    async def fire(deadline_id: int) -> None:
        try:
            runtime_box[0].submit(ExpireTimer(deadline_id=deadline_id), SystemOrigin("scheduler"))
        except Quarantined:
            logging.getLogger(__name__).info("a stale fire found the match already dead")
        except Exception:
            logging.getLogger(__name__).exception("deadline fire failed")

    scheduler = DeadlineScheduler(clock, fire)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, scheduler, broadcaster)
    runtime_box.append(runtime)

    async with _consuming(runtime):
        scheduler.reschedule(state)
        await clock.settle()

        await clock.advance_to(deadline + timedelta(seconds=1))
        await clock.settle()

        assert runtime.state.duel is None, "an on-time expiry must resolve the duel"
        assert len(broadcaster.frames) == 1


# --------------------------------------------------------------------------
# §6.3: a broadcaster failure never quarantines
# --------------------------------------------------------------------------


async def test_a_broadcaster_that_raises_does_not_stop_the_match() -> None:
    """§6.3. The next command must still be accepted and committed."""
    state = _fresh_state()
    clock = FakeClock(BASE_TIME)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = BreakingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), broadcaster)

    first_origin = FutureOrigin()
    first = QueuedCommand.issue(
        CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT), first_origin
    )
    await runtime._consume(first)
    first_outcome = await first_origin.result()
    assert isinstance(first_outcome, Accepted)

    second_origin = FutureOrigin()
    second = QueuedCommand.issue(
        AddPlayer(player_id=PlayerId(uuid4()), name="A", colour="#fff"), second_origin
    )
    await runtime._consume(second)
    second_outcome = await second_origin.result()

    assert isinstance(second_outcome, Accepted), "the second command must still commit"
    assert broadcaster.calls == 2
    assert not runtime.quarantined


async def test_a_broadcaster_that_raises_still_resolves_the_caller() -> None:
    """The caller's command succeeded. Learning otherwise because someone
    else's socket broke would be a lie about durable state."""
    state = _fresh_state()
    clock = FakeClock(BASE_TIME)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    broadcaster = BreakingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), broadcaster)
    origin = FutureOrigin()
    queued = QueuedCommand.issue(
        CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT), origin
    )

    await runtime._consume(queued)

    outcome = await origin.result()
    assert outcome == Accepted((_expected_match_created(),))
    assert broadcaster.calls == 1


# --------------------------------------------------------------------------
# §6.3: a content shortfall is an ordinary refusal; everything else quarantines
# --------------------------------------------------------------------------


async def test_a_failed_outcome_quarantines_and_tells_the_caller() -> None:
    state = _fresh_state()
    clock = FakeClock(BASE_TIME)
    commit = CommitPath(_RecordingUoW(), _RaisingMaterialiser(clock), clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), broadcaster)
    origin = FutureOrigin()
    queued = QueuedCommand.issue(
        CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT), origin
    )

    await runtime._consume(queued)

    outcome = await origin.result()
    assert isinstance(outcome, Failed)
    assert outcome.code is RuntimeCode.INTERNAL
    assert runtime.quarantined
    assert broadcaster.frames == []


async def test_a_content_shortfall_does_not_quarantine() -> None:
    """§6.3 draws exactly this line: «обычный отказ, не авария»."""
    state = _deal_ready_state()
    clock = FakeClock(BASE_TIME)
    bank = FakeCategoryBank(exhaust_after=0)
    materialiser = Materialiser(clock, _NullRepository(), bank, Random(0))
    commit = CommitPath(_RecordingUoW(), materialiser, clock, Random(0))
    broadcaster = RecordingBroadcaster()
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), broadcaster)
    origin = FutureOrigin()
    queued = QueuedCommand.issue(DealBoard(), origin)

    await runtime._consume(queued)

    outcome = await origin.result()
    assert isinstance(outcome, Failed)
    assert outcome.code is RuntimeCode.CONTENT_UNAVAILABLE
    assert not runtime.quarantined
    assert broadcaster.frames == []


async def test_a_quarantined_match_refuses_everything_afterwards() -> None:
    """Including commands already sitting in the queue: each is resolved
    with QUARANTINED rather than left to hang."""
    state = _fresh_state()
    clock = FakeClock(BASE_TIME)
    commit = CommitPath(_RecordingUoW(), _RaisingMaterialiser(clock), clock, Random(0))
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), RecordingBroadcaster())

    pending1, pending2 = FutureOrigin(), FutureOrigin()
    runtime.submit(AddPlayer(player_id=PlayerId(uuid4()), name="A", colour="#000"), pending1)
    runtime.submit(AddPlayer(player_id=PlayerId(uuid4()), name="B", colour="#111"), pending2)

    failing = QueuedCommand.issue(
        CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT), FutureOrigin()
    )
    await runtime._consume(failing)
    assert runtime.quarantined

    outcome1 = await asyncio.wait_for(pending1.result(), timeout=2)
    outcome2 = await asyncio.wait_for(pending2.result(), timeout=2)
    assert outcome1 == Failed(RuntimeCode.QUARANTINED, "this match is quarantined")
    assert outcome2 == Failed(RuntimeCode.QUARANTINED, "this match is quarantined")

    with pytest.raises(Quarantined):
        runtime.submit(
            AddPlayer(player_id=PlayerId(uuid4()), name="C", colour="#222"), FutureOrigin()
        )


# --------------------------------------------------------------------------
# The queue and the consumer
# --------------------------------------------------------------------------


async def test_commands_are_consumed_one_at_a_time_in_order() -> None:
    """«На партию — одна последовательная очередь команд.» Submit several
    at once and assert they were decided against successive states.

    `player_count=2` makes the third `AddPlayer` illegal
    (`PLAYER_COUNT_INVALID`) *only* if `decide` for it sees the first two
    already folded in. `_RecordingUoW`'s `yield_on_append` gives every
    attempt a real suspension point, so a consumer that decided commands
    concurrently instead of one at a time would very likely have the
    third one decide against a state that still shows zero or one player
    and wrongly accept it -- this is not just "the final order looks
    right", it is a correctness property that a single-threaded, one-at-
    a-time consumer must give for free and a concurrent one generally
    cannot.
    """
    state = _fresh_state(player_count=2)
    clock = FakeClock(BASE_TIME)
    uow = _RecordingUoW(yield_on_append=True)
    commit = CommitPath(uow, _materialiser(clock), clock, Random(0))
    runtime = MatchRuntime(state.id, state, commit, _inert_scheduler(clock), RecordingBroadcaster())

    players = [PlayerId(uuid4()) for _ in range(3)]
    origins = [FutureOrigin() for _ in range(3)]

    async with _consuming(runtime):
        for player_id, origin in zip(players, origins, strict=True):
            runtime.submit(AddPlayer(player_id=player_id, name="P", colour="#000"), origin)
        outcomes = await asyncio.gather(*(origin.result() for origin in origins))

    first, second, third = outcomes
    assert isinstance(first, Accepted)
    assert isinstance(second, Accepted)
    assert isinstance(third, Rejected)
    assert third.reason is RejectionReason.PLAYER_COUNT_INVALID, (
        "the third AddPlayer is only illegal if decide saw the first two already folded in"
    )
    assert [player.id for player in runtime.state.players] == players[:2]


async def test_stopping_cancels_the_deadline_task() -> None:
    """A shutdown that leaves a timer armed leaves a task firing into a
    loop nobody is consuming."""
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None
    deadline = deadline_of(state.duel)
    assert deadline is not None

    clock = FakeClock(BASE_TIME)
    fired: list[int] = []

    async def fire(deadline_id: int) -> None:
        fired.append(deadline_id)

    scheduler = DeadlineScheduler(clock, fire)
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    runtime = MatchRuntime(state.id, state, commit, scheduler, RecordingBroadcaster())

    scheduler.reschedule(state)
    await clock.settle()
    assert clock.pending() == 1, "the deadline task must have reached clock.sleep_until"

    runtime.stop()

    assert scheduler.deadline_id is None, "stop() must cancel the armed deadline"
    await clock.settle()
    assert clock.pending() == 0, "the cancelled sleeper must actually be gone"

    await clock.advance_to(deadline)
    assert fired == [], "a cancelled deadline task must never fire"
