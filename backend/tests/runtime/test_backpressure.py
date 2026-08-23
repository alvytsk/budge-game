"""§6.1's `Broadcaster.publish` contract, which its signature cannot
express: only project and `put_nowait`, never await, never block, never
let an exception reach the loop.

`publish` being a plain `def` already rules out `await` by construction --
Python will not let you write one inside a non-async function. What a
signature cannot rule out is a broadcaster that behaves as if it could
block anyway: real blocking I/O inside a `def` is legal Python, and no
type checker sees it. This module cannot drive genuine OS-level blocking
through the loop without hanging the test process itself (and this suite
runs no test that waits on wall-clock time), so what it proves instead is
the concrete, realistic case §6.3 itself names: an outgoing queue that
never drains -- "a client that never reads" -- must never stall the loop,
no matter how many commands run into it.
"""

import asyncio
from collections.abc import Sequence
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.actions import AddPlayer, AssignSecret, DealBoard
from podvinsya.domain.board import BoardSize
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import CategoryId, MatchId, PlayerId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState
from podvinsya.runtime.manager import MatchManager
from podvinsya.runtime.materialiser import Materialiser
from podvinsya.runtime.origins import Accepted
from support.fakes import FakeCategoryBank, FakeClock, Published
from support.streams import BASE_TIME

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BOARD = BoardSize(3, 4)
SETTINGS = MatchSettings()
COLOURS = ("#e5484d", "#3b82f6")


class _NeverReadBroadcaster:
    """One subscriber whose outgoing queue is never drained -- §6.3's own
    "a client that never reads". `publish` does exactly what §6.1 permits
    and nothing else: project into `Published`, then `put_nowait` it.
    `put_nowait` is a plain method on `asyncio.Queue`, not a coroutine --
    there is nothing here for `MatchRuntime._publish` to be stuck behind,
    even once the queue is completely full.

    Once full, `put_nowait` raises `asyncio.QueueFull` straight out of
    `publish` -- exactly the shape a real overflow takes (§6.3: "close
    this one subscriber"). Making sure that never reaches, or stalls, the
    loop is `MatchRuntime._publish`'s job, not this fake's.
    """

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[Published] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = 0

    def publish(
        self, match_id: MatchId, base_seq: int, state: MatchState, events: Sequence[Event]
    ) -> None:
        try:
            self.queue.put_nowait(Published(match_id, base_seq, state, tuple(events)))
        except asyncio.QueueFull:
            self.overflowed += 1
            raise


async def _create_genesis(sessions: async_sessionmaker[AsyncSession]) -> MatchId:
    match_id = MatchId(uuid4())
    created = MatchCreated(board=BOARD, settings=SETTINGS, player_count=2)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    return match_id


def _manager(
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    broadcaster: _NeverReadBroadcaster,
) -> MatchManager:
    def factory() -> Materialiser:
        return Materialiser(clock, MatchRepository(sessions), FakeCategoryBank(), Random(0))

    return MatchManager(
        MatchRepository(sessions),
        UnitOfWork(sessions),
        factory,
        broadcaster,
        clock,
    )


async def test_publish_only_projects_and_never_awaits(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.1's contract, which the signature cannot express: a broadcaster
    that blocks must not be able to stall the loop. Drive a publisher whose
    queue is full and assert the loop's next command still completes within
    a bounded number of clock advances.

    Nothing here ever waits on `sleep_until` -- an overflowing queue is not
    a deadline, so the fake clock never even moves. "Bounded" is therefore
    expressed the same way every other integration test in this suite
    already bounds a background consumer it does not otherwise control:
    `asyncio.wait_for` with a small, fixed timeout. That is not a wall-clock
    wait this property depends on -- a well-behaved loop resolves the
    command in well under a millisecond of real time, backpressure and
    all -- it is the fixed, finite budget "bounded" cashes out to here: a
    genuinely stalled loop times out and fails loudly instead of hanging
    the suite.

    Kills on: `MatchRuntime._publish` waiting on the broadcaster in any way
    that could itself be blocked by a queue nobody drains -- e.g. retrying
    the publish, or routing an overflow into `_quarantine` instead of
    logging and continuing. Either would leave the final `submit` call
    below pending until the timeout below expires, failing the test.
    """
    match_id = await _create_genesis(sessions)
    player_a, player_b = PlayerId(uuid4()), PlayerId(uuid4())
    clock = FakeClock(BASE_TIME)
    broadcaster = _NeverReadBroadcaster(maxsize=1)
    manager = _manager(sessions, clock, broadcaster)
    await manager.start(match_id)

    first = await asyncio.wait_for(
        manager.submit(
            match_id, AddPlayer(player_id=player_a, name="Player 1", colour=COLOURS[0])
        ),
        timeout=2,
    )
    assert isinstance(first, Accepted)
    assert broadcaster.queue.qsize() == 1, (
        "the one slot must genuinely be full before the real test begins"
    )

    # Every further publish now hits a queue nobody ever drains.
    for command in (
        AddPlayer(player_id=player_b, name="Player 2", colour=COLOURS[1]),
        AssignSecret(player_id=player_a, category=CategoryId(uuid4())),
        AssignSecret(player_id=player_b, category=CategoryId(uuid4())),
    ):
        outcome = await asyncio.wait_for(manager.submit(match_id, command), timeout=2)
        assert isinstance(outcome, Accepted), (
            "an overflowing outgoing queue must never fail a command"
        )

    assert broadcaster.overflowed >= 3, (
        "the queue must actually have overflowed on every one of those"
    )

    # The bound itself: the next command's own future must resolve within
    # a small, fixed budget -- not park forever behind a queue that will
    # never make room.
    outcome = await asyncio.wait_for(manager.submit(match_id, DealBoard()), timeout=2)
    assert isinstance(outcome, Accepted)

    await manager.shutdown()
