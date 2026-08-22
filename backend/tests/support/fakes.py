"""Test doubles for every port. The runtime suite is built on these.

`FakeClock` is the reason no test in this plan waits on wall-clock time: a
sleeper parks on an event, and the test decides when the moment arrives.
`settle` exists because a task that has been created has not necessarily
reached its `sleep_until` yet, and a test that advanced time before it did
would see a wake-up that never happened.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from podvinsya.domain.events import Event
from podvinsya.domain.ids import CategoryId, ImageId, MatchId
from podvinsya.domain.state import MatchState
from podvinsya.services.ports import ContentExhausted, Transaction


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._sleepers: list[tuple[datetime, asyncio.Event]] = []

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        if when <= self._now:
            return
        waiter = asyncio.Event()
        self._sleepers.append((when, waiter))
        await waiter.wait()

    async def advance_to(self, when: datetime) -> None:
        """Move time forward and wake everything that was due by then."""
        self._now = when
        due = [(at, waiter) for at, waiter in self._sleepers if at <= when]
        self._sleepers = [(at, w) for at, w in self._sleepers if at > when]
        for _, waiter in due:
            waiter.set()
        await self.settle()

    async def settle(self) -> None:
        """Yield until the loop has run every task that was ready.

        One `sleep(0)` only drains the tasks ready right now; a woken
        sleeper usually schedules more work, so this drains repeatedly. It
        is a scheduling barrier, not a timed wait — nothing here sleeps for
        a duration.
        """
        for _ in range(10):
            await asyncio.sleep(0)

    def pending(self) -> int:
        return len(self._sleepers)


@dataclass
class Published:
    match_id: MatchId
    base_seq: int
    state: MatchState
    events: tuple[Event, ...]


class RecordingBroadcaster:
    """Records what it was told, the way a real broadcaster records into a
    subscriber queue. Never awaits, never raises."""

    def __init__(self) -> None:
        self.frames: list[Published] = []

    def publish(
        self, match_id: MatchId, base_seq: int, state: MatchState, events: Sequence[Event]
    ) -> None:
        self.frames.append(Published(match_id, base_seq, state, tuple(events)))


class BreakingBroadcaster:
    """Raises on every publish. §6.3: a broadcaster failure is logged and
    the match plays on — it never quarantines."""

    def __init__(self) -> None:
        self.calls = 0

    def publish(
        self, match_id: MatchId, base_seq: int, state: MatchState, events: Sequence[Event]
    ) -> None:
        self.calls += 1
        raise RuntimeError("the subscriber's socket is gone")


class FakeCategoryBank:
    """Mints fresh identifiers on demand, and can be told to run dry.

    `exhaust_after` makes the content-shortfall path testable without any
    of plan 6's schema: past that many categories, `draw_categories` raises
    `ContentExhausted`, which §6.3 classifies as an ordinary rejection.
    """

    def __init__(self, *, exhaust_after: int | None = None) -> None:
        self.exhaust_after = exhaust_after
        self.drawn_categories = 0
        self.drawn_packs: list[tuple[CategoryId, int]] = []

    async def draw_categories(
        self, tx: Transaction, count: int, *, exclude: frozenset[CategoryId]
    ) -> tuple[CategoryId, ...]:
        if self.exhaust_after is not None and self.drawn_categories + count > self.exhaust_after:
            raise ContentExhausted(f"asked for {count}, library has fewer")
        self.drawn_categories += count
        drawn: list[CategoryId] = []
        while len(drawn) < count:
            candidate = CategoryId(uuid4())
            if candidate not in exclude:
                drawn.append(candidate)
        return tuple(drawn)

    async def draw_images(
        self, tx: Transaction, category: CategoryId, count: int
    ) -> tuple[ImageId, ...]:
        self.drawn_packs.append((category, count))
        return tuple(ImageId(uuid4()) for _ in range(count))
