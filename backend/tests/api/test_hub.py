"""§6.1's `publish` contract and §11's «Противодавление» row.

The hub is the one place where the command loop touches a socket, so the
tests here are about what it must *not* do: not await, not raise, not grow.
"""

import asyncio
from uuid import uuid4

import pytest

from domain.conftest import build_running_state
from podvinsya.api.hub import MatchHub, Subscriber, Update
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchState

CAPACITY = 4


@pytest.fixture
def state() -> MatchState:
    running, _players = build_running_state()
    return running


def test_publish_is_not_a_coroutine_function() -> None:
    """§6.1: «`publish` синхронный … проецирует и `put_nowait`, ничего
    больше.» Kills on: making it `async`, which the runtime would then
    never await — `MatchRuntime._publish` calls it as a plain call, so an
    async `publish` would produce a never-awaited coroutine and silently
    broadcast nothing at all."""
    assert not asyncio.iscoroutinefunction(MatchHub.publish)


async def test_publish_returns_without_yielding_to_the_loop(state: MatchState) -> None:
    """The stronger form of the test above: a `publish` that awaited
    anywhere inside would let the sentinel task below run.

    Kills on: any `await` reaching into `publish` — a projection, a socket
    write, an `await queue.put` on a bounded queue."""
    hub = MatchHub(capacity=CAPACITY)
    ran = False

    async def sentinel() -> None:
        nonlocal ran
        ran = True

    with hub.subscribe(MatchId(state.id)):
        task = asyncio.create_task(sentinel())
        hub.publish(MatchId(state.id), 0, state, ())
        assert not ran
    await task


async def test_publish_survives_a_subscriber_that_raises(
    state: MatchState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills on: letting the exception escape. It would reach
    `MatchRuntime._publish`, which logs it and plays on — so every
    subscriber *after* the broken one would silently stop receiving for the
    life of the match, and nothing would say so.

    The broken subscriber is first in the list, so a `publish` that stopped
    at the exception would leave the healthy one with nothing."""
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)
    working = Subscriber.offer

    with hub.subscribe(match_id) as broken, hub.subscribe(match_id) as healthy:

        def offer(self: Subscriber, update: Update) -> None:
            if self is broken:
                raise RuntimeError("this socket is gone")
            working(self, update)

        monkeypatch.setattr(Subscriber, "offer", offer)
        hub.publish(match_id, 0, state, ())

        assert broken.pending() == 0
        assert healthy.pending() == 1


def test_publish_to_a_match_with_no_subscribers_is_a_no_op(state: MatchState) -> None:
    """The common case during setup. Kills on: a `KeyError` reaching the
    command loop, which catches it, logs it and plays on — turning a match
    nobody was watching into a match with a logged broadcast failure per
    command."""
    MatchHub(capacity=CAPACITY).publish(MatchId(uuid4()), 0, state, ())


async def test_a_slow_subscriber_loses_its_oldest_frame_and_keeps_the_newest(
    state: MatchState,
) -> None:
    """§11's «Противодавление», and ruling 10's direction.

    Kills on: dropping the *newest* frame, which §7.2 is written to make
    safe in exactly one direction — the last frame is the whole state, and
    a reader that kept the oldest would be permanently behind rather than
    briefly."""
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)

    with hub.subscribe(match_id) as subscriber:
        for base_seq in range(CAPACITY + 3):
            hub.publish(match_id, base_seq, state, ())

        waiting = subscriber.drain()

    assert [update.base_seq for update in waiting] == [3, 4, 5, 6]
    assert subscriber.dropped == 3


async def test_a_slow_subscriber_does_not_delay_publish(state: MatchState) -> None:
    """A client that never reads must not slow the loop by so much as one
    scheduling turn.

    Kills on: `await queue.put` on a bounded queue, which is the natural
    way to write this and would park the command loop on a socket."""
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)

    with hub.subscribe(match_id) as subscriber:
        for base_seq in range(CAPACITY * 25):
            hub.publish(match_id, base_seq, state, ())

        assert subscriber.pending() == CAPACITY
        assert subscriber.dropped == CAPACITY * 24


async def test_unsubscribing_stops_delivery(state: MatchState) -> None:
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)

    with hub.subscribe(match_id) as subscriber:
        pass
    hub.publish(match_id, 0, state, ())

    assert subscriber.pending() == 0
    assert hub.subscriber_count(match_id) == 0


async def test_a_subscriber_removed_by_an_exception_stops_delivery(state: MatchState) -> None:
    """Kills on: removing the subscriber after the yield rather than in a
    `finally`. A socket that dies mid-frame raises out of its writer task,
    and the queue it leaves behind is one nobody will ever drain."""
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)

    with pytest.raises(RuntimeError):
        with hub.subscribe(match_id):
            raise RuntimeError("the socket died mid-frame")

    assert hub.subscriber_count(match_id) == 0


async def test_two_subscribers_of_one_match_both_receive(state: MatchState) -> None:
    """Ruling 9's cost note made concrete: two subscribers, one update
    object, and each one told separately."""
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)

    with hub.subscribe(match_id) as stage, hub.subscribe(match_id) as host:
        hub.publish(match_id, 7, state, ())

        assert stage.pending() == 1
        assert host.pending() == 1
        assert (await stage.next()).base_seq == 7
        assert (await host.next()).base_seq == 7


async def test_a_subscriber_receives_nothing_for_another_match(state: MatchState) -> None:
    """Kills on: keeping one flat subscriber list, which would put every
    match's frames on every socket — including a stage screen watching a
    different game."""
    hub = MatchHub(capacity=CAPACITY)
    other = MatchId(uuid4())

    with hub.subscribe(MatchId(state.id)) as mine:
        hub.publish(other, 0, state, ())

        assert mine.pending() == 0


async def test_an_update_carries_the_batch_and_the_state_it_folded_to(
    state: MatchState,
) -> None:
    """Kills on: publishing the pre-fold state, which would make every
    frame one command stale — the runtime folds *before* it publishes, and
    that ordering is §6.2's requirement, not an implementation detail."""
    hub = MatchHub(capacity=CAPACITY)
    match_id = MatchId(state.id)

    with hub.subscribe(match_id) as subscriber:
        hub.publish(match_id, 12, state, ())
        update = await subscriber.next()

    assert update.base_seq == 12
    assert update.state is state
    assert update.events == ()
