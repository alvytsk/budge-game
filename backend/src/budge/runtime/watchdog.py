"""§4.3's own safety net: catches an armed duel whose timer went missing.

`DeadlineScheduler.reschedule` always cancels the running task and starts a
replacement together, in one call -- there is no code path in this plan
that cancels without recreating. The watchdog exists anyway, as defence in
depth for whichever future bug, exception between the two halves of a
future change, or race that plan review did not anticipate produces that
split regardless. Its condition is not "no deadline" -- a paused duel and a
duel that has not started both have no deadline *legitimately* -- but
exactly «дуэль в фазе RUNNING, паузы нет, дедлайн не запланирован»: an
armed, unpaused duel whose scheduler currently tracks no deadline id.

`MatchRuntime` keeps the `DeadlineScheduler` it consumes commands back
through private, exposing it only through its own read-only `scheduler`
property -- nothing but the manager (task 9), the one place that builds a
match's runtime and scheduler together, ever needs it, and that property is
what lets the manager hand the watchdog `WatchedMatch` pairs rather than
bare runtimes.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from budge.domain.state import DuelPhase
from budge.domain.timing import is_expired
from budge.runtime.match import MatchRuntime
from budge.runtime.scheduler import DeadlineScheduler
from budge.services.ports import Clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WatchedMatch:
    """One live match, paired with the scheduler that is supposed to be
    keeping its deadline armed. Building the pair is the caller's job: the
    scheduler here must be the very one `runtime` consumes `ExpireTimer`
    commands back through, or fixing it fixes nothing."""

    runtime: MatchRuntime
    scheduler: DeadlineScheduler


class Watchdog:
    """Sweeps every live match on `interval` and re-arms whichever ones
    §4.3's three-part condition catches.

    `runtimes` is a zero-argument callable, called once per sweep rather
    than an `Iterable` handed over once at construction time: matches
    start and finish over the process's lifetime, and a snapshot taken
    when the watchdog was built would miss every match that started
    afterwards. `lambda: self._registry.values()` is the shape a real
    caller is expected to pass -- a live view, not a copy.

    `sweep()` is public precisely so a test can drive one pass without
    waiting on `interval`; `run()` is the loop a live deployment starts
    once, in the background, and never gets back until it is cancelled.
    """

    def __init__(
        self,
        clock: Clock,
        interval: timedelta,
        runtimes: Callable[[], Iterable[WatchedMatch]],
    ) -> None:
        self._clock = clock
        self._interval = interval
        self._runtimes = runtimes

    async def sweep(self) -> None:
        now = self._clock.now()
        for watched in self._runtimes():
            self._rearm_if_faulty(watched, now)

    def _rearm_if_faulty(self, watched: WatchedMatch, now: datetime) -> None:
        runtime = watched.runtime
        if runtime.quarantined:
            # It consumes nothing: `MatchRuntime.submit` resolves anything
            # sent its way with `QUARANTINED` without ever touching the
            # scheduler. Arming a timer here would only queue a command
            # that accomplishes nothing.
            return

        duel = runtime.state.duel
        if duel is None:
            return
        if duel.phase is not DuelPhase.RUNNING:
            return
        if duel.paused:
            # §4.1: paused is exactly "phase RUNNING, anchor None" -- a
            # legitimate absent deadline, and the host's own tool. Re-arming
            # it here would take that tool away mid-air.
            return

        scheduler = watched.scheduler
        if scheduler.armed:
            # A task is genuinely alive and covering this deadline;
            # nothing to catch. `deadline_id is not None` alone cannot
            # tell that apart from "fired, task dead" -- `armed` is what
            # actually means covered (see `DeadlineScheduler.armed`).
            return

        # The fault: an armed, unpaused duel with no timer covering it --
        # what a cancel that was never paired with a recreate leaves
        # behind. `reschedule` is the right call whether or not the
        # deadline has already passed: `DeadlineScheduler` sends a
        # past `when` through `clock.sleep_until` and gets back at once,
        # so the duel resolves on the very next turn of the event loop
        # rather than waiting on a future that will never arrive -- the
        # same "expire" outcome §4.2 makes authoritative regardless of
        # which of the two paths got the runtime there. A watchdog that
        # skipped an already-past deadline because "it's too late to arm
        # a meaningful timer" would leave that duel stuck forever instead.
        if is_expired(duel, now):
            logger.warning(
                "watchdog: %s had an already-expired deadline with no timer armed; expiring it",
                runtime.state.id,
            )
        else:
            logger.warning(
                "watchdog: %s was running with no timer armed; re-arming",
                runtime.state.id,
            )
        scheduler.reschedule(runtime.state)

    async def run(self) -> None:
        """The `interval`-driven loop. Runs until cancelled; a caller that
        wants it to stop cancels the task it was started on, the same way
        `MatchRuntime.run` is stopped.

        An exception out of one `sweep()` must not kill this loop: the
        watchdog is defence in depth for every live match at once, so a bug
        in one sweep silently ending the whole loop would take that defence
        away from every match with no signal at all. Log and continue,
        rather than let it propagate -- catching `Exception`, never
        `BaseException`, so cancellation still works.
        """
        while True:
            await self._clock.sleep_until(self._clock.now() + self._interval)
            try:
                await self.sweep()
            except Exception:
                logger.exception("watchdog sweep failed; the loop continues")
