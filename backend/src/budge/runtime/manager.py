"""One `MatchRuntime` per live match, owned for the lifetime of the
process: started on first touch, held in a private registry keyed by
`MatchId`, and let go of cleanly -- every consumer task, the watchdog, and
every armed deadline -- when the process shuts down.

`recover` (task 7) does all the wiring for one match; this module's only
job is to make sure that wiring runs at most once per match id and that
whatever it starts is torn down without leaving anyone hanging. §6: «на
партию -- одна последовательная очередь команд» -- the registry below is
what keeps that true across two calls to `start` for the same match, not
the optimistic append underneath it: catching a duplicate writer there is a
failure path, and not having one is the design.

`self._start_lock` serialises `start`'s "is this match already live, and if
not, recover it" sequence. Without it, two concurrent `start` calls for the
same not-yet-started match id could both see an empty registry, both pass
the `MatchAlreadyRunning` check, and both call `recover` -- the second
`_live[match_id] = ...` would then silently clobber the first, leaking a
runtime and its consumer task that nothing will ever cancel. The lock is
process-wide rather than per-match: starting a match happens once in its
whole lifetime, so serialising that rare transition costs nothing once
matches are running, and a single lock is the plain way to give
`MatchAlreadyRunning`'s own guard a registry it does not have to race
against.

`shutdown` takes the very same lock, for the very same reason, against a
different race: `start` awaits real I/O (`recover`) while holding it, so a
`shutdown` that did not wait its turn could snapshot `self._live` and tear
everything down *before* an in-flight `start` ever registers its runtime --
leaving that runtime's consumer task, and possibly the watchdog task
`start` also lazily creates, running forever with nothing left to cancel
them. Sharing the lock forces one of two orderings, never a third: either
`shutdown` runs first and `start` (which checks `self._closed` the instant
it acquires the lock) never calls `recover` at all, or an in-flight `start`
finishes registering first and `shutdown`, unblocked immediately after,
finds that registration in its snapshot and tears it down like any other.
The second case does mean a `start` racing a concurrent `shutdown` and
winning that race still returns a `MatchRuntime` to its caller -- one that
`shutdown` stops moments later. That caller has no way to know this except
by trying to `submit` against it afterwards and getting `KeyError`, the
same as for any match id this manager has never heard of; there is no
window in which anything about it is left running unaccounted for, which is
the guarantee that matters. A `start` that begins strictly after `shutdown`
raises `ManagerShuttingDown` instead, and never touches `recover`.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
from random import Random

from budge.domain.actions import Command
from budge.domain.ids import MatchId
from budge.runtime.errors import ManagerShuttingDown, MatchAlreadyRunning
from budge.runtime.materialiser import Materialiser
from budge.runtime.match import MatchRuntime
from budge.runtime.origins import CommandOutcome, FutureOrigin
from budge.runtime.recovery import recover
from budge.runtime.watchdog import Watchdog, WatchedMatch
from budge.services.ports import (
    Broadcaster,
    Clock,
    MatchRepositoryPort,
    RuntimeCode,
    UnitOfWorkPort,
)

logger = logging.getLogger(__name__)

_DEFAULT_WATCHDOG_INTERVAL = timedelta(seconds=30)
_SHUTDOWN_MESSAGE = "the manager is shutting down"


@dataclass(slots=True)
class _Live:
    """One running match: its runtime, the task consuming its queue, and
    whoever this manager currently has waiting on a command submitted
    through it.

    `pending` is tracked here rather than on `MatchRuntime` -- which
    already resolves everything it knows about, including commands the
    manager never sees, such as a deadline's own `ExpireTimer` -- because
    only the manager needs to reach every *manager-issued* waiter at once,
    on shutdown, before their consumer task is cancelled out from under
    them.
    """

    runtime: MatchRuntime
    consumer: "asyncio.Task[None]"
    pending: set[FutureOrigin] = field(default_factory=set)


class MatchManager:
    """Owns the registry §6 calls for: one queue per match, never two, for
    as long as this process runs."""

    def __init__(
        self,
        repository: MatchRepositoryPort,
        uow: UnitOfWorkPort,
        materialiser_factory: Callable[[], Materialiser],
        broadcaster: Broadcaster,
        clock: Clock,
        *,
        watchdog_interval: timedelta = _DEFAULT_WATCHDOG_INTERVAL,
    ) -> None:
        self._repository = repository
        self._uow = uow
        self._materialiser_factory = materialiser_factory
        self._broadcaster = broadcaster
        self._clock = clock
        self._watchdog_interval = watchdog_interval
        self._live: dict[MatchId, _Live] = {}
        self._start_lock = asyncio.Lock()
        self._closed = False
        # Built lazily, on the first call to `start`, rather than here: a
        # bare `MatchManager(...)` has no side effects, and
        # `asyncio.create_task` needs a running loop that construction time
        # cannot promise.
        self._watchdog_task: asyncio.Task[None] | None = None

    def runtime_for(self, match_id: MatchId) -> MatchRuntime | None:
        live = self._live.get(match_id)
        return live.runtime if live is not None else None

    async def start(self, match_id: MatchId) -> MatchRuntime:
        """Recover `match_id` and start consuming its queue.

        Raises `MatchAlreadyRunning` for a match already live, and
        `ManagerShuttingDown` if `shutdown` has already begun -- see the
        module docstring for why both checks, and `recover` itself, run
        under the same lock `shutdown` takes.
        """
        async with self._start_lock:
            if self._closed:
                raise ManagerShuttingDown(f"cannot start {match_id}: the manager is shutting down")
            if match_id in self._live:
                raise MatchAlreadyRunning(f"{match_id} is already running")
            return await self._start_locked(match_id)

    async def submit(self, match_id: MatchId, command: Command) -> CommandOutcome:
        """Queue `command` on `match_id`'s runtime and hand back whatever
        the loop decided -- verbatim, not a summary of it.

        `match_id` must already be live -- `start` is the manager's own
        first-touch hook, and calling it is the caller's job, not this
        method's; a `match_id` nobody has started raises `KeyError` on the
        registry lookup below.
        """
        live = self._live[match_id]
        origin = FutureOrigin()
        live.pending.add(origin)
        try:
            live.runtime.submit(command, origin)
            return await origin.result()
        finally:
            live.pending.discard(origin)

    async def shutdown(self) -> None:
        """Cancel every background task this manager ever started -- one
        consumer per match, plus the watchdog -- and every deadline they
        were keeping, then release every caller still waiting on an
        outcome.

        Waiters are resolved before their consumer is cancelled, not after:
        cancelling first would abandon whichever command was mid-flight
        with nothing to ever tell its caller. `FutureOrigin.resolve_failed`
        is idempotent (`services.ports.Origin`'s own contract), so a
        command that happens to finish on its own in the narrow window
        between the two costs nothing -- the caller already has an answer,
        and the real one is simply discarded.

        Held under `self._start_lock` for its entire body, not just the
        registry snapshot: see the module docstring for why sharing the
        lock with `start` is what stops an in-flight `start` from
        registering a runtime this method has no way to know about and so
        no way to tear down.
        """
        async with self._start_lock:
            self._closed = True

            watchdog_task, self._watchdog_task = self._watchdog_task, None
            if watchdog_task is not None:
                watchdog_task.cancel()

            live_matches = list(self._live.values())
            self._live.clear()
            for live in live_matches:
                for origin in live.pending:
                    origin.resolve_failed(RuntimeCode.INTERNAL, _SHUTDOWN_MESSAGE)
                live.runtime.stop()
                live.consumer.cancel()

            if watchdog_task is not None:
                with suppress(asyncio.CancelledError):
                    await watchdog_task
            for live in live_matches:
                with suppress(asyncio.CancelledError):
                    await live.consumer

    def _watched_matches(self) -> Iterable[WatchedMatch]:
        """The live callable `Watchdog` (task 8) sweeps: called once per
        sweep and built fresh every time, the shape `Watchdog.__init__`
        asks for, so a match that starts or finishes between sweeps is
        never missed by a snapshot taken once at construction time.

        `runtime.scheduler` (added alongside this module) is what makes
        this possible without reimplementing recovery's own wiring:
        `recover` builds a runtime and a scheduler together but hands back
        only the runtime, and nothing before this task ever needed the
        scheduler back out of it.
        """
        return [WatchedMatch(live.runtime, live.runtime.scheduler) for live in self._live.values()]

    async def _start_locked(self, match_id: MatchId) -> MatchRuntime:
        """The actual recovery-and-registration; callers hold
        `self._start_lock` before reaching here."""
        materialiser = self._materialiser_factory()
        runtime = await recover(
            match_id,
            self._repository,
            self._uow,
            materialiser,
            self._clock,
            Random(),
            self._broadcaster,
        )
        consumer = asyncio.create_task(runtime.run())
        self._live[match_id] = _Live(runtime, consumer)
        self._ensure_watchdog()
        return runtime

    def _ensure_watchdog(self) -> None:
        if self._watchdog_task is not None:
            return
        watchdog = Watchdog(self._clock, self._watchdog_interval, self._watched_matches)
        self._watchdog_task = asyncio.create_task(watchdog.run())
