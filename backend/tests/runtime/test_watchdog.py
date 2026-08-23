"""§4.3's watchdog: catches an armed duel whose timer went missing.

`DeadlineScheduler.reschedule` always cancels the running task and starts
its replacement together, in one call -- there is no code path anywhere in
this plan that cancels a deadline without recreating it. Every test here
manufactures the fault directly instead: it pairs a running, unpaused
duel's state with a fresh `DeadlineScheduler` that simply never had
`reschedule` called on it. That is not a state today's code can reach on
its own; it is what a bug or a race that splits cancel from recreate would
leave behind, which is exactly what the watchdog exists to notice as
defence in depth. See the task report for the fuller argument.

None of these tests route commands through `MatchRuntime.run`'s real
queue-and-consumer -- the watchdog's fix is `DeadlineScheduler.reschedule`,
called directly on the scheduler paired with each runtime, so nothing here
needs a background consumer task. Only the quarantine test drives
`MatchRuntime._consume` for real, the same way `tests/runtime/test_match.py`
does, because `quarantined` is state only a real `Failed` outcome produces.
"""

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from random import Random
from uuid import uuid4

import pytest

from budge.db.repository import LoadedMatch
from budge.domain.actions import AddPlayer, Command
from budge.domain.board import BoardSize
from budge.domain.context import DecisionContext
from budge.domain.events import AttackDeclared, DuelPaused, DuelStarted, Event, MatchCreated
from budge.domain.evolve import fold
from budge.domain.genesis import create_initial_state
from budge.domain.ids import MatchId, PlayerId
from budge.domain.settings import MatchSettings
from budge.domain.state import DuelPhase, MatchState
from budge.domain.timing import deadline_of
from budge.runtime.commit import CommitPath
from budge.runtime.match import MatchRuntime
from budge.runtime.materialiser import Materialiser
from budge.runtime.origins import FutureOrigin, QueuedCommand
from budge.runtime.scheduler import Callback, DeadlineScheduler
from budge.runtime.watchdog import Watchdog, WatchedMatch
from budge.services.ports import Reconciliation, Transaction
from support.fakes import FakeCategoryBank, FakeClock, RecordingBroadcaster
from support.streams import BASE_TIME, Recorded, build_rich_stream

# The logger name below is a string, and `caplog.at_level` accepts any
# string: a name that no module produces captures nothing and the
# assertion then fails with an empty-records message that reads like a
# behaviour change. Import the module that owns the logger instead, so a
# rename that orphans the name fails here and says why.
importlib.import_module("budge.runtime.watchdog")

_BOARD = BoardSize(3, 4)
_SETTINGS = MatchSettings()


# --------------------------------------------------------------------------
# Shared doubles -- deliberately minimal, local copies rather than an import
# from test_match.py's own private classes, matching that module's own
# precedent of not sharing fakes across test files.
# --------------------------------------------------------------------------


class _RecordingTransaction:
    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        return None


class _RecordingUoW:
    """Nothing here is ever called by the tests in this module -- every
    `CommitPath` built below exists only because `MatchRuntime.__init__`
    requires one, not because any test drives it. The quarantine test is
    the one exception; `_RaisingMaterialiser` fails before this is ever
    reached, so it stays this simple."""

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Transaction]:
        yield _RecordingTransaction()

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        raise AssertionError("reconcile must not be called by anything in this module")


class _NullRepository:
    async def create(self, match_id: MatchId, event: MatchCreated, *, operation_id: str) -> None:
        raise NotImplementedError("no test in this module reads the repository")

    async def read_events(self, match_id: MatchId) -> tuple[Event, ...]:
        raise NotImplementedError("no test in this module reads the repository")

    async def load(self, match_id: MatchId) -> LoadedMatch:
        raise NotImplementedError("no test in this module reads the repository")


class _RaisingMaterialiser(Materialiser):
    """Every `build` call raises -- the shortest path to a
    `Failed(RuntimeCode.INTERNAL, ...)` outcome, which is what
    `MatchRuntime._consume` turns into `quarantined`."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock, _NullRepository(), FakeCategoryBank(), Random(0))

    async def build(
        self, state: MatchState, command: Command, tx: Transaction, *, at: datetime | None = None
    ) -> DecisionContext:
        raise RuntimeError("materialiser blew up")


def _materialiser(clock: FakeClock) -> Materialiser:
    return Materialiser(clock, _NullRepository(), FakeCategoryBank(), Random(0))


def _fold_prefix(match_id: MatchId, events: Sequence[Event]) -> MatchState:
    genesis = events[0]
    assert isinstance(genesis, MatchCreated)
    return fold(create_initial_state(match_id, genesis.board, genesis.settings), events)


def _state_after(recorded: Recorded, event_type: type) -> MatchState:
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type)) + 1
    return _fold_prefix(recorded.state.id, recorded.events[:cut])


def _watch(state: MatchState, scheduler: DeadlineScheduler, clock: FakeClock) -> WatchedMatch:
    """Pair a runtime holding `state` with `scheduler` -- the exact object
    a real caller would build together and never let drift apart. Nothing
    in the tests that use this drives `MatchRuntime._consume`, so the
    commit path underneath is never exercised; it exists only because
    `MatchRuntime.__init__` requires one."""
    commit = CommitPath(_RecordingUoW(), _materialiser(clock), clock, Random(0))
    runtime = MatchRuntime(state.id, state, commit, scheduler, RecordingBroadcaster())
    return WatchedMatch(runtime, scheduler)


class _NowCountingClock:
    """Wraps a `FakeClock`, counting real calls to `now` -- proof that the
    watchdog's own fault check reads the current instant off the clock
    port rather than the wall clock. `DeadlineScheduler` itself never
    calls `now`, only `sleep_until`, so nothing but the watchdog's own code
    can move this counter."""

    def __init__(self, inner: FakeClock) -> None:
        self._inner = inner
        self.now_calls = 0

    def now(self) -> datetime:
        self.now_calls += 1
        return self._inner.now()

    async def sleep_until(self, when: datetime) -> None:
        await self._inner.sleep_until(when)


def _one(watched: WatchedMatch) -> Callable[[], list[WatchedMatch]]:
    return lambda: [watched]


class _CountingScheduler(DeadlineScheduler):
    """Counts calls to `reschedule` itself, not just their net effect on
    `deadline_id` -- `DeadlineScheduler.reschedule` is defensive on its
    own (a no-op for a paused duel, a no-op for a redundant call with the
    same id already scheduled), so watching `deadline_id` alone cannot
    tell "the watchdog skipped this pair" apart from "the watchdog called
    reschedule and it declined to do anything." Whether the watchdog is
    supposed to call it at all is exactly what the three-part condition
    decides, so the call itself is what a test has to watch."""

    def __init__(self, clock: FakeClock, fire: Callback) -> None:
        super().__init__(clock, fire)
        self.reschedule_calls = 0

    def reschedule(self, state: MatchState) -> None:
        self.reschedule_calls += 1
        super().reschedule(state)


# --------------------------------------------------------------------------
# The tests
# --------------------------------------------------------------------------


async def test_a_running_duel_with_no_deadline_is_re_armed() -> None:
    """The fault §4.3 exists to catch: an armed, unpaused duel whose
    scheduler was never told to keep a deadline for it."""
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None and not state.duel.paused
    deadline = deadline_of(state.duel)
    assert deadline is not None

    clock = FakeClock(BASE_TIME)
    fired: list[int] = []

    async def fire(deadline_id: int) -> None:
        fired.append(deadline_id)

    scheduler = DeadlineScheduler(clock, fire)  # never armed: the fault
    watched = _watch(state, scheduler, clock)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await clock.settle()

    assert scheduler.deadline_id == state.seq, "the watchdog must arm the missing timer"
    assert clock.pending() == 1, "the deadline is in the future -- nothing should fire yet"
    assert fired == []

    await clock.advance_to(deadline)
    assert fired == [state.seq]


async def test_a_paused_duel_is_left_alone() -> None:
    """The whole reason the condition is three-part rather than „no
    deadline". A watchdog that re-armed a pause would take the host's only
    tool away from them mid-air.

    `DeadlineScheduler.reschedule` happens to be defensive about a paused
    duel on its own -- `deadline_of` returns None for one, so `reschedule`
    just cancels and returns. That means checking the net effect on
    `scheduler.deadline_id` alone cannot tell "the watchdog skipped this
    duel" apart from "the watchdog called reschedule and it declined to
    arm anything" -- both leave `deadline_id` at None. `_CountingScheduler`
    counts the call itself, which is the one thing the three-part
    condition is actually responsible for.
    """
    recorded = build_rich_stream()
    paused = _state_after(recorded, DuelPaused)
    assert paused.duel is not None and paused.duel.paused

    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        raise AssertionError("must never fire a paused duel's timer")

    # Unscheduled -- exactly what a real pause leaves behind (§4.1), and
    # indistinguishable from the fault by `deadline_id` alone.
    scheduler = _CountingScheduler(clock, fire)
    watched = _watch(paused, scheduler, clock)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await clock.settle()

    assert scheduler.reschedule_calls == 0, "a paused duel must never even be handed to reschedule"
    assert scheduler.deadline_id is None
    assert clock.pending() == 0


async def test_a_duel_with_a_deadline_is_left_alone() -> None:
    """`DeadlineScheduler.reschedule` is itself a no-op for a redundant
    call carrying the same deadline id already scheduled, so watching
    `deadline_id` -- or even `clock.sleep_until` call counts -- cannot
    tell "the watchdog correctly skipped this pair" apart from "the
    watchdog called reschedule again and it declined to churn." Counting
    the call to `reschedule` itself is the only way to see which one
    happened; whether it is called at all is what the condition decides.
    """
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None

    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        pass

    scheduler = _CountingScheduler(clock, fire)
    scheduler.reschedule(state)  # properly armed already
    await clock.settle()
    assert scheduler.reschedule_calls == 1, "the test needs a real task already scheduled to start"

    watched = _watch(state, scheduler, clock)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await clock.settle()

    assert scheduler.reschedule_calls == 1, "an already-armed deadline must never reach reschedule"
    assert scheduler.deadline_id == state.seq


async def test_a_fired_but_dead_timer_is_caught_by_armed_not_deadline_id() -> None:
    """Important 3, the watchdog's own half. `deadline_id` is cleared only
    by `cancel()` -- deliberately, since `_is_stale_timer` needs it after
    the fact -- so it cannot tell a genuinely armed timer from one that
    already fired and left its task dead. `MatchRuntime._consume`'s own
    `NoOp`-branch fix (Important 3's other half) closes this the moment
    such a command is actually consumed; this is what closes it as
    defence in depth if, for any reason, that never happens. Manufactured
    directly, the same way every other fault in this module is: let a
    real task fire and finish on its own, leaving `deadline_id` set but
    `armed` False -- exactly what a naive `deadline_id is not None` check
    would mistake for "already covered."
    """
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None and not state.duel.paused
    deadline = deadline_of(state.duel)
    assert deadline is not None

    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        pass  # the task simply finishes; nothing resubmits an ExpireTimer

    scheduler = _CountingScheduler(clock, fire)
    scheduler.reschedule(state)
    await clock.settle()
    assert scheduler.reschedule_calls == 1

    await clock.advance_to(deadline)
    await clock.settle()
    assert scheduler.deadline_id == state.seq, "only cancel() may clear the id"
    assert not scheduler.armed, "the test needs the task to have genuinely finished"

    # The clock stepped backward before the watchdog's next sweep -- the
    # same §4.1 hazard, so the re-armed task gets a deadline genuinely in
    # the future and survives past this sweep instead of firing again at
    # once.
    await clock.advance_to(BASE_TIME)

    watched = _watch(state, scheduler, clock)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await clock.settle()

    assert scheduler.reschedule_calls == 2, "a fired-but-dead timer must be re-armed"
    assert scheduler.armed, "the re-arm must leave a genuinely live task behind"


async def test_a_match_with_no_duel_is_left_alone() -> None:
    """Two shapes of "no duel", both legitimate: no duel object at all,
    and a duel that exists but has not started -- `AttackDeclared` sets
    `phase=DECLARED, anchor=None` (§3: a duel is declared before it is
    started), which has no clock yet exactly as legitimately as a duel
    that has not been declared at all."""
    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        raise AssertionError("must never fire when there is no running duel")

    no_duel = create_initial_state(MatchId(uuid4()), _BOARD, _SETTINGS)
    assert no_duel.duel is None
    scheduler_a = DeadlineScheduler(clock, fire)
    watched_a = _watch(no_duel, scheduler_a, clock)

    recorded = build_rich_stream()
    declared = _state_after(recorded, AttackDeclared)
    assert declared.duel is not None and declared.duel.phase is DuelPhase.DECLARED
    scheduler_b = _CountingScheduler(clock, fire)
    watched_b = _watch(declared, scheduler_b, clock)

    watchdog = Watchdog(clock, timedelta(seconds=5), lambda: [watched_a, watched_b])

    await watchdog.sweep()
    await clock.settle()

    assert scheduler_a.deadline_id is None
    assert scheduler_b.reschedule_calls == 0, "a declared-but-not-started duel must be left alone"
    assert scheduler_b.deadline_id is None
    assert clock.pending() == 0


async def test_a_quarantined_match_is_left_alone() -> None:
    """It consumes nothing; arming a timer for it queues a command that
    will only be resolved with QUARANTINED."""
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None and not state.duel.paused

    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        raise AssertionError("must never arm a quarantined match's timer")

    # Unscheduled -- looks exactly like the fault from the outside.
    scheduler = DeadlineScheduler(clock, fire)
    commit = CommitPath(_RecordingUoW(), _RaisingMaterialiser(clock), clock, Random(0))
    runtime = MatchRuntime(state.id, state, commit, scheduler, RecordingBroadcaster())
    queued = QueuedCommand.issue(
        AddPlayer(player_id=PlayerId(uuid4()), name="X", colour="#000000"), FutureOrigin()
    )
    await runtime._consume(queued)
    assert runtime.quarantined, "the test needs a genuinely quarantined runtime"

    watched = WatchedMatch(runtime, scheduler)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await clock.settle()

    assert scheduler.deadline_id is None
    assert clock.pending() == 0


async def test_a_deadline_already_past_expires_rather_than_re_arming() -> None:
    """Re-arming a deadline that has already passed schedules a sleep that
    returns immediately and fires anyway — going straight to the expiry is
    the same outcome without the detour, and it is what §4.2 requires: the
    clock is authoritative."""
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None and not state.duel.paused
    deadline = deadline_of(state.duel)
    assert deadline is not None

    clock = FakeClock(deadline + timedelta(seconds=5))  # already past when the fault is caught
    fired: list[int] = []

    async def fire(deadline_id: int) -> None:
        fired.append(deadline_id)

    scheduler = DeadlineScheduler(clock, fire)  # never armed: the fault
    watched = _watch(state, scheduler, clock)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await clock.settle()

    assert fired == [state.seq], "an already-past deadline must resolve, not just sit scheduled"
    assert clock.pending() == 0, "nothing should be left waiting once the fault is caught this late"


async def test_the_sweep_runs_on_the_clock_port() -> None:
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)
    assert state.duel is not None
    deadline = deadline_of(state.duel)
    assert deadline is not None

    inner = FakeClock(BASE_TIME)
    clock = _NowCountingClock(inner)
    fired: list[int] = []

    async def fire(deadline_id: int) -> None:
        fired.append(deadline_id)

    scheduler = DeadlineScheduler(clock, fire)
    watched = _watch(state, scheduler, inner)
    watchdog = Watchdog(clock, timedelta(seconds=5), _one(watched))

    await watchdog.sweep()
    await inner.settle()

    assert clock.now_calls >= 1, "the fault check must read the current instant off the clock port"
    assert scheduler.deadline_id == state.seq
    assert fired == [], "the deadline is in the future -- nothing should fire yet"

    await inner.advance_to(deadline)
    assert fired == [state.seq], "advancing the fake clock -- never real time -- is what fires it"


# --------------------------------------------------------------------------
# Critical 2's other half: a sweep that raises must not kill the loop
# --------------------------------------------------------------------------


async def test_a_sweep_that_raises_is_logged_and_the_loop_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§4.3's defence in depth is worthless if one bad sweep ends the whole
    loop for every other live match with no signal at all. `run()` must log
    and continue, not let the exception escape."""
    clock = FakeClock(BASE_TIME)

    def _raising_runtimes() -> list[WatchedMatch]:
        raise RuntimeError("sweep blew up")

    watchdog = Watchdog(clock, timedelta(seconds=5), _raising_runtimes)
    task = asyncio.create_task(watchdog.run())
    try:
        await clock.settle()  # let the loop reach its first sleep_until
        assert clock.pending() == 1, "the loop must have registered its first wait"

        with caplog.at_level(logging.ERROR, logger="budge.runtime.watchdog"):
            await clock.advance_to(BASE_TIME + timedelta(seconds=5))
            await clock.settle()

        assert "watchdog sweep failed" in caplog.text
        assert any(record.exc_info is not None for record in caplog.records), (
            "the log record should carry the exception, not just a bare message"
        )
        assert not task.done(), "one failing sweep must not kill the watchdog's own loop"
        assert clock.pending() == 1, (
            "the loop must still be waiting on the clock for the next sweep"
        )
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
