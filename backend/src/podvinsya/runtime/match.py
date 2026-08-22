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

from podvinsya.domain.actions import Command, ExpireTimer
from podvinsya.domain.evolve import fold
from podvinsya.domain.events import Event
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchState
from podvinsya.runtime.commit import CommitPath
from podvinsya.runtime.errors import Quarantined
from podvinsya.runtime.origins import Accepted, Failed, NoOp, QueuedCommand, Rejected
from podvinsya.runtime.scheduler import DeadlineScheduler
from podvinsya.services.ports import Broadcaster, Origin, RuntimeCode

logger = logging.getLogger(__name__)


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

    def submit(self, command: Command, origin: Origin) -> None:
        """Mint this command's identity (§5.1: always here, for every
        command, without exception) and queue it for the consumer.

        A match that has already been quarantined refuses outright: §6's
        `Quarantined` is a state that "stops consuming its queue and
        refuses new commands", and queuing one anyway would just be a
        second, slower way to hang the caller -- the consumer that would
        eventually resolve it has already stopped looking.
        """
        if self._quarantined:
            raise Quarantined(f"match {self._match_id} is quarantined")
        self._queue.put_nowait(QueuedCommand.issue(command, origin))

    async def run(self) -> None:
        """§6.2's loop: pull one command, process it, repeat -- forever,
        until this task is cancelled from outside. Nothing externally
        visible happens under a lock: `CommitPath.run` returns only after
        its transaction has closed, so everything `_consume` does with the
        result runs with no lock held."""
        while True:
            queued = await self._queue.get()
            await self._consume(queued)

    def stop(self) -> None:
        """Cancel whatever deadline is armed. A shutdown that leaves a
        timer armed leaves a task firing into a loop nobody is
        consuming."""
        self._scheduler.cancel()

    async def _consume(self, queued: QueuedCommand) -> None:
        if self._quarantined:
            queued.origin.resolve_failed(RuntimeCode.QUARANTINED, "this match is quarantined")
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
            queued.origin.resolve_failed(RuntimeCode.QUARANTINED, "this match is quarantined")
