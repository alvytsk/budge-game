"""One bounded queue per subscriber, fed by a `publish` that cannot wait.

§6.1 makes `Broadcaster.publish` synchronous on purpose: the command loop
is forbidden to wait on a socket write, and a `def` cannot be accidentally
awaited. This module is the implementation of that contract, and it holds
three properties that its signature cannot:

*No await.* `publish` enqueues the immutable `(base_seq, state, events)`
triple and returns. Projection needs the content directory, which needs
I/O, so ruling 9 moves it into each subscriber's own writer task — where it
is allowed to await, and where a frame dropped under backpressure is never
projected at all.

*No exception escapes.* A subscriber whose socket has already gone must not
stop the next subscriber from being told, and must never reach the command
loop — which catches and logs it (§6.3) and then plays on, leaving the
*other* subscribers silently unfed.

*No unbounded growth.* The queue is bounded, and on overflow it loses its
oldest waiting frame (ruling 10). That is safe only because §7.2 makes
every message carry the whole state — and it is the reason §7.2 exists.
"""

import asyncio
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from podvinsya.domain.events import Event
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Update:
    """Exactly what §6.1's `publish` is handed, kept as a value.

    Immutable, and every field in it already is: `MatchState` is frozen and
    `events` is a tuple. So one update can sit in four subscribers' queues
    at once with nobody able to change what the others will read.
    """

    base_seq: int
    state: MatchState
    events: tuple[Event, ...]


@dataclass(slots=True)
class Subscriber:
    """One reader's bounded queue, and the count of what it has missed."""

    match_id: MatchId
    capacity: int
    dropped: int = 0
    _queue: asyncio.Queue[Update] = field(default_factory=asyncio.Queue)

    def offer(self, update: Update) -> None:
        """Enqueue, dropping the oldest waiting update if the queue is full.

        Never awaits and never raises: a full queue is a slow reader, which
        §7.2 makes a narration problem, not a correctness one. The drop is
        logged at debug, not at warning — ruling 10 is explicit that it is
        not an error, and a warning per frame would bury the log of a
        genuinely broken match.
        """
        while self._queue.qsize() >= self.capacity:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - single-threaded loop
                break
            self.dropped += 1
            logger.debug(
                "subscriber of %s is behind; dropped the oldest frame (%d total)",
                self.match_id,
                self.dropped,
            )
        self._queue.put_nowait(update)

    async def next(self) -> Update:
        return await self._queue.get()

    def pending(self) -> int:
        return self._queue.qsize()

    def drain(self) -> tuple[Update, ...]:
        """Everything waiting, oldest first.

        The backpressure test's reader: asserting *which* frames survived
        an overflow is the whole of ruling 10, and it cannot be done one
        `next()` at a time without the test blocking on the frame that was
        dropped. No production caller drains — a writer that coalesced
        would discard narration the queue had room for, which is a wider
        loss than the one §7.2 licenses.
        """
        updates: list[Update] = []
        while True:
            try:
                updates.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return tuple(updates)


class MatchHub:
    """The `Broadcaster` §6.1 asks for, satisfied structurally.

    Subscribers are held per match rather than in one flat list so a
    publish costs the length of one match's subscriber list — two, in this
    deployment — instead of a scan over every socket in the process.
    """

    def __init__(self, *, capacity: int) -> None:
        self._capacity = capacity
        self._subscribers: dict[MatchId, list[Subscriber]] = {}

    @contextmanager
    def subscribe(self, match_id: MatchId) -> Iterator[Subscriber]:
        """Attach a subscriber for the duration of the block.

        The removal is in a `finally`, not after the yield: a socket that
        dies mid-frame raises out of the writer task, and a subscriber left
        attached after that is a queue nobody drains, growing to `capacity`
        and then dropping frames forever for a reader that no longer exists.
        """
        subscriber = Subscriber(match_id=match_id, capacity=self._capacity)
        self._subscribers.setdefault(match_id, []).append(subscriber)
        try:
            yield subscriber
        finally:
            readers = self._subscribers.get(match_id)
            if readers is not None:
                if subscriber in readers:
                    readers.remove(subscriber)
                if not readers:
                    del self._subscribers[match_id]

    def subscriber_count(self, match_id: MatchId) -> int:
        return len(self._subscribers.get(match_id, ()))

    def publish(
        self,
        match_id: MatchId,
        base_seq: int,
        state: MatchState,
        events: Sequence[Event],
    ) -> None:
        """§6.1, verbatim: project nothing, await nothing, raise nothing.

        A match nobody is watching is the common case during setup, and it
        costs one dictionary lookup that finds nothing.
        """
        readers = self._subscribers.get(match_id)
        if not readers:
            return
        update = Update(base_seq=base_seq, state=state, events=tuple(events))
        for subscriber in tuple(readers):
            try:
                subscriber.offer(update)
            except Exception:
                # One broken subscriber must not cost the next one its
                # frame. Letting this escape would reach `MatchRuntime.
                # _publish`, which logs it and plays on — meaning every
                # subscriber after this one silently stops receiving, for
                # the life of the match, with nothing saying so.
                logger.exception("a subscriber of %s could not be offered a frame", match_id)
