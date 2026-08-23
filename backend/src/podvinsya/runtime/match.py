"""One live match's command loop: §6.2, made executable.

`CommitPath` owns everything up to and including the commit. This module
owns everything after it: folding the accepted batch into memory, rearming
the deadline, publishing the frame, and resolving whoever is waiting -- and
the order among those four is the requirement, not an implementation
detail. Publishing before the deadline is rearmed would advertise a timer
the runtime is not yet keeping; resolving before publishing would let a
caller act on an outcome the room has not seen yet.
"""

import asyncio
import logging
from collections.abc import Callable

from podvinsya.domain.actions import Command, ExpireTimer
from podvinsya.domain.evolve import fold
from podvinsya.domain.events import Event
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchState
from podvinsya.runtime.commit import CommitPath
from podvinsya.runtime.origins import (
    Accepted,
    Failed,
    NoOp,
    QueuedCommand,
    Rejected,
    SystemOrigin,
)
from podvinsya.runtime.scheduler import Callback, DeadlineScheduler
from podvinsya.services.ports import Broadcaster, Origin, RuntimeCode

logger = logging.getLogger(__name__)

_QUARANTINED_MESSAGE = "this match is quarantined"


class MatchRuntime:
    """One live match: one queue, one consumer, one deadline."""

    def __init__(
        self,
        match_id: MatchId,
        state: MatchState,
        commit_path: CommitPath,
        scheduler: DeadlineScheduler,
        broadcaster: Broadcaster,
    ) -> None:
        self._match_id = match_id
        self._state = state
        self._commit = commit_path
        self._scheduler = scheduler
        self._broadcaster = broadcaster
        self._queue: asyncio.Queue[QueuedCommand] = asyncio.Queue()
        self._quarantined = False

    @property
    def state(self) -> MatchState:
        return self._state

    @property
    def quarantined(self) -> bool:
        return self._quarantined

    @property
    def scheduler(self) -> DeadlineScheduler:
        """The exact `DeadlineScheduler` this runtime consumes `ExpireTimer`
        commands back through -- exposed for the manager (task 9), which is
        the one place that builds a `WatchedMatch` pairing this runtime with
        this scheduler for the watchdog. `recover` hands back only a
        runtime, never the scheduler it built alongside it, so this is the
        only way the manager can recover that pairing without reimplementing
        recovery's own wiring."""
        return self._scheduler

    def submit(self, command: Command, origin: Origin) -> None:
        """Mint this command's identity (§5.1: always here, for every
        command, without exception) and queue it for the consumer.

        §6.2 names quarantine as one of the four ways an origin is
        resolved -- alongside no-op, rejection and success -- so a match
        that has already been quarantined resolves the origin with
        `QUARANTINED` immediately, the same way `_consume` resolves a
        command that was already queued when quarantine struck. Raising
        instead would give a caller two different error idioms for the
        one outcome, chosen by nothing but arrival-time luck.
        """
        if self._quarantined:
            origin.resolve_failed(RuntimeCode.QUARANTINED, _QUARANTINED_MESSAGE)
            return
        self._queue.put_nowait(QueuedCommand.issue(command, origin))

    async def run(self) -> None:
        """§6.2's loop: pull one command, process it, repeat -- forever,
        until this task is cancelled from outside. Nothing externally
        visible happens under a lock: `CommitPath.run` returns only after
        its transaction has closed, so everything `_consume` does with the
        result runs with no lock held.

        Everything below the commit line -- `fold`, `scheduler.reschedule`,
        `origin.resolve_ok` -- runs unguarded inside `_consume`. §6.3 names
        «исключение в decide / evolve → карантин» as its own row, but
        `evolve` runs here, outside `CommitPath`, so this `try` is the other
        half of that row: without it, an exception here would escape this
        loop entirely and kill the consumer task silently -- `quarantined`
        would stay `False`, `submit` would keep accepting commands into a
        queue nobody drains, and every later caller would park on a future
        that never resolves. Catching `Exception`, never `BaseException`,
        keeps cancellation propagating normally.
        """
        while True:
            queued = await self._queue.get()
            try:
                await self._consume(queued)
            except Exception as exc:
                logger.exception(
                    "match %s: an exception escaped _consume; quarantining", self._match_id
                )
                self._quarantine(repr(exc))
                queued.origin.resolve_failed(RuntimeCode.QUARANTINED, _QUARANTINED_MESSAGE)

    def stop(self) -> None:
        """Cancel whatever deadline is armed. A shutdown that leaves a
        timer armed leaves a task firing into a loop nobody is
        consuming."""
        self._scheduler.cancel()

    async def _consume(self, queued: QueuedCommand) -> None:
        if self._quarantined:
            queued.origin.resolve_failed(RuntimeCode.QUARANTINED, _QUARANTINED_MESSAGE)
            return
        if self._is_stale_timer(queued.command):
            # §4.3: the identifier is what makes cancellation races
            # harmless. This is a benign race, so it is a no-op and not a
            # rejection -- a rejection would show the host an error for
            # something the server did to itself.
            queued.origin.resolve_noop()
            return

        outcome = await self._commit.run(self._state, queued)
        match outcome:
            case NoOp():
                queued.origin.resolve_noop()
            case Rejected(reason):
                queued.origin.resolve_rejected(reason)
            case Failed(code, message):
                if code is not RuntimeCode.CONTENT_UNAVAILABLE:
                    self._quarantine(message)
                queued.origin.resolve_failed(code, message)
            case Accepted(events):
                base_seq = self._state.seq
                self._state = fold(self._state, events)
                self._scheduler.reschedule(self._state)
                self._publish(base_seq, events)
                queued.origin.resolve_ok(events)

    def _is_stale_timer(self, command: Command) -> bool:
        """`Duel` does not record which seq set its anchor, so the domain
        cannot make this check (see `ExpireTimer.deadline_id`'s own
        comment) -- the scheduler's current `deadline_id` is the runtime's
        record of it instead."""
        if not isinstance(command, ExpireTimer):
            return False
        return command.deadline_id != self._scheduler.deadline_id

    def _publish(self, base_seq: int, events: tuple[Event, ...]) -> None:
        """§6.3: a broadcaster failure is logged and the match plays on.

        The commit is durable and memory is correct. Killing a live match
        because one subscriber's socket broke would turn a client's
        problem into the whole room's.
        """
        try:
            self._broadcaster.publish(self._match_id, base_seq, self._state, events)
        except Exception:
            logger.exception("broadcast failed for %s; the match continues", self._match_id)

    def _quarantine(self, message: str) -> None:
        """Stop trusting this process with the match. §6: nothing is
        written to the log when this happens -- the log is what we still
        trust -- but every origin already queued must still hear back:
        a command left sitting in a queue nobody consumes is a hung
        request."""
        if self._quarantined:
            return
        self._quarantined = True
        self._scheduler.cancel()
        logger.error("match %s quarantined: %s", self._match_id, message)
        self._drain_queue()

    def _drain_queue(self) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            queued.origin.resolve_failed(RuntimeCode.QUARANTINED, _QUARANTINED_MESSAGE)


def wire_deadline_fire(submit: Callable[[Command, Origin], None]) -> Callback:
    """Build the `fire` callback that connects a `DeadlineScheduler` back
    to a `MatchRuntime`'s queue.

    `MatchRuntime.__init__` takes an already-built scheduler, so this is
    the pattern a caller must use to assemble the two: define `fire`
    wrapped around a not-yet-constructed runtime's `submit` (a small
    forwarding closure, or a one-element list filled in right after the
    runtime is built, breaks the construction cycle), pass `fire` into
    `DeadlineScheduler(clock, fire)`, then build the runtime.

    `DeadlineScheduler._sleep_and_fire` awaits `fire` directly, so
    anything it raises becomes an exception on the scheduler's own
    background task -- an exception asyncio logs through its default
    exception handler, never as a `warnings`-module warning, so it is
    invisible to a suite that leans on `pytest -W error` for pristine
    output. Catching everything here and logging it is what keeps a
    stale-timer race, or any other submission failure, from silently
    killing that task.
    """

    async def fire(deadline_id: int) -> None:
        try:
            submit(ExpireTimer(deadline_id=deadline_id), SystemOrigin("scheduler"))
        except Exception:
            logger.exception("deadline fire failed")

    return fire
