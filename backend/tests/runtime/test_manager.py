"""`MatchManager`: one `MatchRuntime` per live match, started on first
touch, and let go of cleanly when the process shuts down.

Every test here starts a match through `MatchManager.start` or
`MatchManager.submit`, both of which recover it through the real `recover`
(task 7) against a real, persisted log -- the same way a server bootstrap
does -- so every test in this module is integration-marked, matching
`tests/runtime/test_recovery.py`'s own precedent. None of them constructs a
`MatchState` by hand.
"""

import asyncio
from collections.abc import Sequence
from datetime import datetime
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.actions import AddPlayer, Command, ResumeDuel
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.errors import RejectionReason
from podvinsya.domain.events import DuelStarted, Event, MatchCreated
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId, PlayerId
from podvinsya.domain.state import MatchState
from podvinsya.runtime.errors import MatchAlreadyRunning
from podvinsya.runtime.manager import MatchManager
from podvinsya.runtime.materialiser import Materialiser
from podvinsya.runtime.origins import Accepted, Failed, FutureOrigin, Rejected
from podvinsya.services.ports import RuntimeCode, Transaction
from support.fakes import FakeCategoryBank, FakeClock, RecordingBroadcaster
from support.streams import BASE_TIME, Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# --------------------------------------------------------------------------
# Persisting a (possibly truncated) stream, and folding the same prefix in
# memory. Local copies, matching `tests/runtime/test_recovery.py`'s own
# precedent of not sharing test fakes across files.
# --------------------------------------------------------------------------


async def _persist(
    sessions: async_sessionmaker[AsyncSession], match_id: MatchId, events: Sequence[Event]
) -> None:
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1:]):
        async with uow.begin() as tx:
            await tx.append(
                match_id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )


def _fold_prefix(match_id: MatchId, events: Sequence[Event]) -> MatchState:
    genesis = events[0]
    assert isinstance(genesis, MatchCreated)
    return fold(create_initial_state(match_id, genesis.board, genesis.settings), events)


async def _persisted_prefix(
    sessions: async_sessionmaker[AsyncSession], recorded: Recorded, cut: int
) -> MatchState:
    truncated = recorded.events[:cut]
    state = _fold_prefix(recorded.state.id, truncated)
    await _persist(sessions, recorded.state.id, truncated)
    return state


def _cut_after(recorded: Recorded, event_type: type) -> int:
    return next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type)) + 1


def _add_player(name: str) -> AddPlayer:
    return AddPlayer(player_id=PlayerId(uuid4()), name=name, colour="#111111")


def _manager(
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    *,
    seed: int = 0,
) -> MatchManager:
    def factory() -> Materialiser:
        return Materialiser(clock, MatchRepository(sessions), FakeCategoryBank(), Random(seed))

    return MatchManager(
        MatchRepository(sessions),
        UnitOfWork(sessions),
        factory,
        RecordingBroadcaster(),
        clock,
    )


class _RaisingMaterialiser(Materialiser):
    """Every `build` call raises -- the shortest path to a
    `Failed(RuntimeCode.INTERNAL, ...)` outcome, which is what
    `MatchRuntime._consume` turns into `quarantined`."""

    def __init__(self, clock: FakeClock, sessions: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(clock, MatchRepository(sessions), FakeCategoryBank(), Random(0))

    async def build(
        self, state: MatchState, command: Command, tx: Transaction, *, at: datetime | None = None
    ) -> DecisionContext:
        raise RuntimeError("materialiser blew up")


class _BlockingMaterialiser(Materialiser):
    """`build` marks `entered` the instant it starts, then waits on
    `released` -- an event the test never sets. This is the shortest way to
    get a caller genuinely parked on `FutureOrigin.result()` with its
    consumer task suspended *inside* a command, rather than merely queued
    behind one."""

    def __init__(
        self,
        clock: FakeClock,
        sessions: async_sessionmaker[AsyncSession],
        entered: asyncio.Event,
        released: asyncio.Event,
    ) -> None:
        super().__init__(clock, MatchRepository(sessions), FakeCategoryBank(), Random(0))
        self._entered = entered
        self._released = released

    async def build(
        self, state: MatchState, command: Command, tx: Transaction, *, at: datetime | None = None
    ) -> DecisionContext:
        self._entered.set()
        await self._released.wait()
        return await super().build(state, command, tx, at=at)


# --------------------------------------------------------------------------
# The tests
# --------------------------------------------------------------------------


async def test_starting_a_match_recovers_it_and_runs_a_consumer(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    prefix = await _persisted_prefix(sessions, recorded, _cut_after(recorded, MatchCreated))

    clock = FakeClock(BASE_TIME)
    manager = _manager(sessions, clock)

    runtime = await manager.start(prefix.id)
    assert runtime.state == prefix, "start must recover the persisted log, not a blank state"
    assert manager.runtime_for(prefix.id) is runtime

    origin = FutureOrigin()
    runtime.submit(_add_player("A"), origin)
    outcome = await asyncio.wait_for(origin.result(), timeout=2)
    assert isinstance(outcome, Accepted), "a background consumer must be draining the queue"

    await manager.shutdown()


async def test_starting_the_same_match_twice_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6 gives each match one sequential queue. The optimistic append
    would catch a second writer, but catching it is a failure path."""
    recorded = build_rich_stream()
    prefix = await _persisted_prefix(sessions, recorded, _cut_after(recorded, MatchCreated))

    clock = FakeClock(BASE_TIME)
    manager = _manager(sessions, clock)

    await manager.start(prefix.id)
    with pytest.raises(MatchAlreadyRunning):
        await manager.start(prefix.id)

    await manager.shutdown()


async def test_submitting_returns_the_outcome_the_loop_produced(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    prefix = await _persisted_prefix(sessions, recorded, _cut_after(recorded, MatchCreated))
    assert not prefix.players

    clock = FakeClock(BASE_TIME)
    manager = _manager(sessions, clock)
    await manager.start(prefix.id)

    first = await manager.submit(prefix.id, _add_player("A"))
    assert isinstance(first, Accepted)
    second = await manager.submit(prefix.id, _add_player("B"))
    assert isinstance(second, Accepted)
    # build_rich_stream's genesis fixes player_count at 2 -- a third
    # AddPlayer is only illegal if the loop actually folded the first two
    # in before deciding this one.
    third = await manager.submit(prefix.id, _add_player("C"))
    assert isinstance(third, Rejected), (
        "the manager must forward whatever the loop decided, not a canned outcome"
    )
    assert third.reason is RejectionReason.PLAYER_COUNT_INVALID

    runtime = manager.runtime_for(prefix.id)
    assert runtime is not None
    assert len(runtime.state.players) == 2

    await manager.shutdown()


async def test_two_matches_run_independently(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A quarantine in one must not touch the other -- they share a process
    and nothing else."""
    recorded_a = build_rich_stream()
    recorded_b = build_rich_stream()
    prefix_a = await _persisted_prefix(sessions, recorded_a, _cut_after(recorded_a, MatchCreated))
    prefix_b = await _persisted_prefix(sessions, recorded_b, _cut_after(recorded_b, MatchCreated))

    clock = FakeClock(BASE_TIME)
    materialisers = iter(
        [
            _RaisingMaterialiser(clock, sessions),
            Materialiser(clock, MatchRepository(sessions), FakeCategoryBank(), Random(0)),
        ]
    )
    manager = MatchManager(
        MatchRepository(sessions),
        UnitOfWork(sessions),
        lambda: next(materialisers),
        RecordingBroadcaster(),
        clock,
    )

    await manager.start(prefix_a.id)
    await manager.start(prefix_b.id)

    a_outcome = await manager.submit(prefix_a.id, _add_player("A"))
    assert isinstance(a_outcome, Failed)
    assert a_outcome.code is RuntimeCode.INTERNAL

    runtime_a = manager.runtime_for(prefix_a.id)
    runtime_b = manager.runtime_for(prefix_b.id)
    assert runtime_a is not None and runtime_b is not None
    assert runtime_a.quarantined, "match A must be quarantined by its own failure"
    assert not runtime_b.quarantined, "a quarantine in one match must not touch the other"

    b_outcome = await manager.submit(prefix_b.id, _add_player("B"))
    assert isinstance(b_outcome, Accepted), "match B must keep working after match A is quarantined"

    await manager.shutdown()


async def test_shutdown_cancels_every_consumer_and_every_deadline(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A shutdown that leaves a timer armed leaves a task firing into a
    loop nobody is consuming."""
    recorded = build_rich_stream()
    cut = _cut_after(recorded, DuelStarted)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is not None and prefix.duel.anchor is not None
    anchor = prefix.duel.anchor

    clock = FakeClock(anchor)
    manager = _manager(sessions, clock)

    runtime = await manager.start(prefix.id)
    assert runtime.state.duel is not None and runtime.state.duel.paused, (
        "recovery must have paused the duel that was running"
    )

    outcome = await manager.submit(prefix.id, ResumeDuel())
    assert isinstance(outcome, Accepted), (
        "the test needs resuming to succeed, to arm a real deadline"
    )
    await clock.settle()

    scheduler = runtime.scheduler
    assert scheduler.deadline_id is not None, "the test needs a genuinely armed deadline"
    consumer = manager._live[prefix.id].consumer
    assert not consumer.done(), "the test needs a genuinely running consumer"

    await manager.shutdown()

    assert scheduler.deadline_id is None, "shutdown must cancel the armed deadline"
    assert consumer.done(), "shutdown must cancel the consumer task"
    assert clock.pending() == 0, "no sleeper -- deadline or watchdog -- may be left behind"


async def test_shutdown_resolves_whoever_was_still_waiting(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A caller awaiting a future that nobody will ever complete is a hung
    request that survives the process it was made to."""
    recorded = build_rich_stream()
    prefix = await _persisted_prefix(sessions, recorded, _cut_after(recorded, MatchCreated))

    clock = FakeClock(BASE_TIME)
    entered = asyncio.Event()
    released = asyncio.Event()
    materialiser = _BlockingMaterialiser(clock, sessions, entered, released)
    manager = MatchManager(
        MatchRepository(sessions),
        UnitOfWork(sessions),
        lambda: materialiser,
        RecordingBroadcaster(),
        clock,
    )
    await manager.start(prefix.id)

    submit_task = asyncio.create_task(manager.submit(prefix.id, _add_player("A")))
    await asyncio.wait_for(entered.wait(), timeout=2)
    # The caller above is now genuinely parked: its command is mid-flight
    # inside the materialiser, which will never return on its own.

    await asyncio.wait_for(manager.shutdown(), timeout=2)

    outcome = await asyncio.wait_for(submit_task, timeout=2)
    assert isinstance(outcome, Failed), (
        "shutdown must resolve the waiting caller instead of leaving it parked forever"
    )
