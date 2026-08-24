"""One task, one deadline, and an identifier that makes staleness harmless.

Every test here drives `FakeClock` explicitly: `settle()` is used after
every `reschedule()`/`cancel()` to let a created or cancelled task reach its
next suspension point before an assertion looks at it, and `pending()` is
used as proof that a wait actually went through `clock.sleep_until` rather
than resolving some other way.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from budge.domain.board import BoardSize
from budge.domain.events import (
    AnswerAccepted,
    DuelPaused,
    DuelStarted,
    Event,
    MatchCreated,
    MatchReset,
)
from budge.domain.evolve import fold
from budge.domain.genesis import create_initial_state
from budge.domain.ids import MatchId
from budge.domain.settings import MatchSettings
from budge.domain.state import MatchState
from budge.domain.timing import deadline_of
from budge.runtime.scheduler import DeadlineScheduler
from support.fakes import FakeClock
from support.streams import BASE_TIME, Recorded, build_rich_stream


class _CountingClock:
    """Wraps a `FakeClock` and counts real calls to `sleep_until`.

    Some tests need to prove a task was, or was not, cancelled and
    recreated. Counting the wait calls is a black-box way to see that
    without reaching into the scheduler's own task reference.
    """

    def __init__(self, inner: FakeClock) -> None:
        self._inner = inner
        self.sleep_calls = 0

    def now(self) -> datetime:
        return self._inner.now()

    async def sleep_until(self, when: datetime) -> None:
        self.sleep_calls += 1
        await self._inner.sleep_until(when)


def _fold_prefix(match_id: MatchId, events: Sequence[Event]) -> MatchState:
    """Recover the state a truncated event prefix implies, the same way
    recovery does: fold onto `create_initial_state` seeded from genesis."""
    genesis = events[0]
    assert isinstance(genesis, MatchCreated)
    return fold(create_initial_state(match_id, genesis.board, genesis.settings), events)


def _state_after(recorded: Recorded, event_type: type) -> MatchState:
    """The state immediately after the first event of `event_type`."""
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type)) + 1
    return _fold_prefix(recorded.state.id, recorded.events[:cut])


async def test_a_running_duel_gets_a_task_that_fires_at_its_deadline() -> None:
    """The deadline is derived, never stored: anchor plus the answering
    player's remaining time."""
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
    scheduler.reschedule(state)
    await clock.settle()
    assert clock.pending() == 1, "the task must have reached clock.sleep_until"

    await clock.advance_to(deadline - deadline.resolution)
    assert fired == [], "must not fire before its deadline"

    await clock.advance_to(deadline)
    assert fired == [state.seq]


async def test_nothing_is_scheduled_for_a_paused_duel() -> None:
    """«Пауза — легальное состояние „дедлайна нет“»: the task is cancelled
    and not recreated."""
    recorded = build_rich_stream()
    running = _state_after(recorded, AnswerAccepted)
    paused = _state_after(recorded, DuelPaused)
    assert paused.duel is not None and paused.duel.paused

    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        raise AssertionError("must never fire once the duel is paused")

    scheduler = DeadlineScheduler(clock, fire)
    scheduler.reschedule(running)
    await clock.settle()
    assert clock.pending() == 1, "must have a task scheduled before the pause"

    scheduler.reschedule(paused)
    await clock.settle()

    assert clock.pending() == 0, "a paused duel must cancel the running task"
    assert scheduler.deadline_id is None
    assert scheduler.scheduled_for is None


async def test_nothing_is_scheduled_when_there_is_no_duel() -> None:
    state = create_initial_state(MatchId(uuid4()), BoardSize(3, 4), MatchSettings())
    assert state.duel is None

    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        raise AssertionError("must never fire when there is no duel")

    scheduler = DeadlineScheduler(clock, fire)
    scheduler.reschedule(state)
    await clock.settle()

    assert clock.pending() == 0
    assert scheduler.deadline_id is None
    assert scheduler.scheduled_for is None


async def test_rescheduling_to_the_same_deadline_id_keeps_the_task() -> None:
    """A re-publish or a redundant call must not churn the task -- every
    cancel/recreate is a window where the watchdog sees no deadline."""
    recorded = build_rich_stream()
    state = _state_after(recorded, DuelStarted)

    inner = FakeClock(BASE_TIME)
    clock = _CountingClock(inner)

    async def fire(deadline_id: int) -> None:
        pass

    scheduler = DeadlineScheduler(clock, fire)
    scheduler.reschedule(state)
    await inner.settle()
    assert clock.sleep_calls == 1

    scheduler.reschedule(state)  # same seq, same anchor: a redundant call
    await inner.settle()
    assert clock.sleep_calls == 1, "a redundant reschedule must not cancel and recreate the task"


async def test_a_new_deadline_id_replaces_the_task() -> None:
    recorded = build_rich_stream()
    first = _state_after(recorded, DuelStarted)
    second = _state_after(recorded, AnswerAccepted)
    assert second.seq != first.seq
    assert second.duel is not None

    inner = FakeClock(BASE_TIME)
    clock = _CountingClock(inner)

    async def fire(deadline_id: int) -> None:
        pass

    scheduler = DeadlineScheduler(clock, fire)
    scheduler.reschedule(first)
    await inner.settle()
    assert clock.sleep_calls == 1

    scheduler.reschedule(second)
    await inner.settle()

    assert clock.sleep_calls == 2, "a new deadline id must cancel the old task and start a new one"
    assert scheduler.deadline_id == second.seq
    assert scheduler.scheduled_for == deadline_of(second.duel)


async def test_the_replaced_task_does_not_fire() -> None:
    """The point of the identifier is that a stale task is harmless, but a
    stale task that still fires wastes a queue slot on every judgement."""
    recorded = build_rich_stream()
    first = _state_after(recorded, DuelStarted)
    second = _state_after(recorded, AnswerAccepted)
    assert first.duel is not None and second.duel is not None
    deadline_first = deadline_of(first.duel)
    deadline_second = deadline_of(second.duel)
    assert deadline_first is not None and deadline_second is not None
    assert deadline_first < deadline_second, "the test needs the old deadline to come first"

    clock = FakeClock(BASE_TIME)
    fired: list[int] = []

    async def fire(deadline_id: int) -> None:
        fired.append(deadline_id)

    scheduler = DeadlineScheduler(clock, fire)
    scheduler.reschedule(first)
    await clock.settle()
    assert clock.pending() == 1

    scheduler.reschedule(second)
    await clock.settle()
    assert clock.pending() == 1, "the new task must be pending; the old one must be gone"

    await clock.advance_to(deadline_first)
    assert fired == [], "the replaced task's deadline arrived, but it must not fire"

    await clock.advance_to(deadline_second)
    assert fired == [second.seq]


async def test_the_scheduler_fires_with_the_deadline_id_it_slept_on() -> None:
    """`ExpireTimer(deadline_id)` carries it so the runtime can drop a
    stale one. Firing with the current id instead would make the check
    tautological."""
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
    scheduler.reschedule(state)
    await clock.settle()
    assert clock.pending() == 1

    # A correct implementation can never produce this on its own --
    # reschedule always cancels the running task before moving the id on --
    # but it is exactly the situation a naive `await self._fire(self._deadline_id)`
    # would get wrong instead of capturing the id in the sleeping task's own
    # closure. Poking the private field is how the test tells the two
    # implementations apart.
    scheduler._deadline_id = 999999

    await clock.advance_to(deadline)
    assert fired == [state.seq], (
        "must fire with the id it slept on, not whatever self._deadline_id reads now"
    )


async def test_cancelling_twice_is_harmless() -> None:
    """Shutdown calls it, and so does a pause that arrives first."""
    recorded = build_rich_stream()
    running = _state_after(recorded, DuelStarted)
    clock = FakeClock(BASE_TIME)

    async def fire(deadline_id: int) -> None:
        pass

    scheduler = DeadlineScheduler(clock, fire)

    # Never scheduled at all -- shutdown before anything ever ran.
    scheduler.cancel()
    scheduler.cancel()

    scheduler.reschedule(running)
    await clock.settle()
    assert clock.pending() == 1

    scheduler.cancel()  # a pause arriving first
    scheduler.cancel()  # shutdown, right behind it
    await clock.settle()

    assert clock.pending() == 0
    assert scheduler.deadline_id is None
    assert scheduler.scheduled_for is None


async def test_the_scheduler_never_sleeps_on_the_wall_clock() -> None:
    """Every wait in this module goes through the clock port. Drive the
    fake clock past the deadline and the task must fire; leave it alone and
    the task must not."""
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
    scheduler.reschedule(state)
    await clock.settle()
    assert clock.pending() == 1, "the wait must be registered on the clock port, not elsewhere"
    assert fired == [], "nothing may fire while the fake clock stands still"

    await clock.advance_to(deadline)
    assert fired == [state.seq]


async def test_a_reset_disarms_the_deadline() -> None:
    """§A.5: `reschedule` reads state, and a state with no duel has no
    deadline -- so the task is dropped on the same general path, without a
    single line that knows about resets.

    Kills on: a scheduler that drops tasks off a list of known event types
    -- a reset mid-duel would leave the timer alive, and a minute later it
    would resolve a duel that no longer exists.
    """

    async def _never_fires(_deadline_id: int) -> None:
        raise AssertionError("the deadline must not fire in this test")

    recorded = build_rich_stream()
    duelling = _state_after(recorded, DuelStarted)
    clock = FakeClock(BASE_TIME)
    scheduler = DeadlineScheduler(clock, _never_fires)

    scheduler.reschedule(duelling)
    await clock.settle()
    assert scheduler.armed is True

    reset = fold(duelling, (MatchReset(keep_roster=True),))
    scheduler.reschedule(reset)
    await clock.settle()
    assert scheduler.armed is False
    assert scheduler.deadline_id is None
    assert reset.duel is None
