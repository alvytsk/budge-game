"""One task, one deadline, and an identifier that makes staleness harmless.

The identifier is the `seq` of the event that set the current anchor. A
timer that fires carrying an identifier the runtime has moved past is
dropped, so nothing here depends on a cancellation winning its race — which
is the only way to make this correct without a lock the loop cannot hold.

Why `state.seq` names a deadline: `evolve` assigns seq by folding one event
at a time, so the seq recorded on a state is the seq of the very last event
that produced it. When that state's duel carries a freshly set anchor — via
`DuelStarted`, `AnswerAccepted`, `PassUsed` or `DuelResumed`, whichever last
touched it — that last event *is* the one that set the anchor, so
`state.seq` is exactly the identifier a later `ExpireTimer(deadline_id)`
must be compared against. It costs nothing extra to track: it is already on
every state the runtime holds.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from podvinsya.domain.state import MatchState
from podvinsya.domain.timing import deadline_of
from podvinsya.services.ports import Clock

Callback = Callable[[int], Awaitable[None]]


class DeadlineScheduler:
    """Sleeps until a duel's deadline, then calls `fire` with the
    identifier that deadline was scheduled under.

    At most one `asyncio.Task` is alive at a time. `reschedule` decides
    whether the state it is given implies a different deadline than the one
    already scheduled and, if so, cancels the old task and starts a new
    one; if not, it leaves the task alone.
    """

    def __init__(self, clock: Clock, fire: Callback) -> None:
        self._clock = clock
        self._fire = fire
        self._task: asyncio.Task[None] | None = None
        self._deadline_id: int | None = None
        self._scheduled_for: datetime | None = None
        # Tasks that have been cancelled but have not yet run their final
        # step. Cancelling a task and then dropping the only reference to
        # it risks the garbage collector reclaiming it before the loop
        # delivers CancelledError -- exactly how "Task was destroyed but it
        # is pending!" warnings happen. Keeping each cancelled task alive
        # here until it actually finishes avoids that without forcing
        # cancel(), a synchronous method, to await anything.
        self._retiring: set[asyncio.Task[None]] = set()

    @property
    def deadline_id(self) -> int | None:
        return self._deadline_id

    @property
    def armed(self) -> bool:
        """Whether a task is actually alive and covering `deadline_id`.

        `deadline_id` alone cannot tell that apart from "fired, task dead":
        it is cleared only by `cancel()`, deliberately, because
        `_is_stale_timer` needs it after the fact to recognise the timer
        that just fired. `armed` is the check that means what §4.3 says --
        an armed, unpaused duel with a task genuinely covering it -- and is
        what the watchdog (`_rearm_if_faulty`) checks instead of
        `deadline_id is not None`.
        """
        return self._task is not None and not self._task.done()

    @property
    def scheduled_for(self) -> datetime | None:
        return self._scheduled_for

    def reschedule(self, state: MatchState) -> None:
        """Point the task at whatever deadline this state implies.

        A state with no duel, or a paused one (§4.1: `paused` is exactly
        "phase RUNNING, anchor None"), means no deadline: cancel and do not
        recreate. `deadline_of` already returns None for both, so a single
        check covers both cases.

        A state whose deadline identifier is the one already scheduled
        means leave the task alone -- churning it on every redundant call
        would open a window, however brief, where the watchdog sees an
        armed duel with no timer covering it.
        """
        duel = state.duel
        deadline = deadline_of(duel) if duel is not None else None
        if deadline is None:
            self.cancel()
            return

        new_id = state.seq
        if new_id == self._deadline_id and self._task is not None and not self._task.done():
            return

        self.cancel()
        self._deadline_id = new_id
        self._scheduled_for = deadline
        self._task = asyncio.create_task(self._sleep_and_fire(deadline, new_id))

    def cancel(self) -> None:
        """Cancel whatever is scheduled, if anything.

        Idempotent, and safe to call when nothing was ever scheduled: a
        pause can cancel the task first, and shutdown cancels again right
        behind it, and neither call may raise or double-cancel.
        """
        task = self._task
        self._task = None
        self._deadline_id = None
        self._scheduled_for = None
        if task is None or task.done():
            return
        task.cancel()
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    async def _sleep_and_fire(self, when: datetime, deadline_id: int) -> None:
        """`deadline_id` is a plain argument, captured in this coroutine's
        own closure -- not read off `self._deadline_id` after the sleep
        returns. By the time `sleep_until` wakes, a later `reschedule` may
        have moved `self._deadline_id` on; reading it at that point would
        fire with whatever is current rather than what this task actually
        slept on, which would make the consumer's staleness check compare
        an identifier against itself.
        """
        await self._clock.sleep_until(when)
        await self._fire(deadline_id)
