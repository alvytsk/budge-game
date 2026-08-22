"""The clock is the only place the runtime is allowed to learn the time."""

import asyncio
from datetime import UTC, datetime, timedelta

from podvinsya.runtime.clock import SystemClock
from support.fakes import FakeClock

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_the_system_clock_reports_an_aware_utc_instant() -> None:
    """Every datetime that reaches an event is walked for tzinfo by the
    codec, which refuses a naive one. The clock is where they all come
    from, so it is where UTC is guaranteed."""
    moment = SystemClock().now()
    assert moment.tzinfo is UTC


async def test_a_sleep_until_in_the_past_returns_at_once() -> None:
    """A deadline already passed is the normal case after a slow commit,
    not an error: the loop asks to sleep and gets control straight back."""
    clock = SystemClock()
    await clock.sleep_until(clock.now() - timedelta(seconds=5))


async def test_the_fake_clock_does_not_move_on_its_own() -> None:
    clock = FakeClock(NOW)
    assert clock.now() == NOW
    assert clock.now() == NOW


async def test_a_fake_sleeper_wakes_only_when_time_reaches_its_deadline() -> None:
    """This is what lets every later test drive a deadline without waiting:
    the sleeper is parked until the test says the moment has arrived."""
    clock = FakeClock(NOW)
    woke = False

    async def sleeper() -> None:
        nonlocal woke
        await clock.sleep_until(NOW + timedelta(seconds=60))
        woke = True

    task = asyncio.create_task(sleeper())
    await clock.settle()
    assert not woke, "nothing may wake before its deadline"

    await clock.advance_to(NOW + timedelta(seconds=59))
    assert not woke

    await clock.advance_to(NOW + timedelta(seconds=60))
    assert woke
    await task


async def test_advancing_past_several_deadlines_wakes_all_of_them() -> None:
    clock = FakeClock(NOW)
    woken: list[int] = []

    async def sleeper(seconds: int) -> None:
        await clock.sleep_until(NOW + timedelta(seconds=seconds))
        woken.append(seconds)

    tasks = [asyncio.create_task(sleeper(s)) for s in (10, 20, 30)]
    await clock.settle()
    await clock.advance_to(NOW + timedelta(seconds=25))
    assert sorted(woken) == [10, 20]
    await clock.advance_to(NOW + timedelta(seconds=30))
    assert sorted(woken) == [10, 20, 30]
    for task in tasks:
        await task
