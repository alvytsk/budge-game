# Рантайм «Подвинься» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a pure domain and a durable log into a running match — one sequential command queue per match, a clock that owns the deadlines, and a failure policy that never lets a broken socket kill a live game.

**Architecture:** One worker. `MatchManager` holds a `MatchRuntime` per live match; each runtime owns one queue and one consumer loop. The loop opens a transaction, materialises every non-deterministic input as a value, calls the pure `decide`, appends, and commits — then, and only then, folds state, reschedules the deadline, publishes, and resolves whoever was waiting. Every capability the loop needs is a `Protocol` in `services/ports.py`, so the tests drive it with a fake clock, a recording broadcaster and a breaking commit, and no test waits on wall-clock time.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy 2.0 (async) over the persistence layer built in plan 2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md` — §6 is this plan's mandate; §4.2, §4.3 and §4.4 bound its time model, and §3.4/§3.5/§3.7 say what the materialiser owes the domain.

**Branch:** `feature/runtime`, cut from `feature/persistence` (commit `043bd42`), which is not yet merged to `main`. Everything under `src/podvinsya/db/` is that branch's work and is not modified here.

## Global Constraints

- Python `>=3.12`. `mypy --strict` clean over `src/podvinsya` and `tests`; `ruff check` clean with `select = ["E4", "E7", "E9", "F", "E501"]` and `line-length = 100`.
- Dependency direction is one-way: `api → services → domain`, with `runtime` and `db` as service-layer implementations. **No file under `src/podvinsya/domain/` or `src/podvinsya/db/` is modified by this plan.**
- «Все capability объявлены как `Protocol` в `services/ports.py`; реализаций под `services/` нет.» (§6.1) The protocols live there; the classes satisfying them live under `runtime/` and `db/`.
- «`sleep_until`, а не `sleep`» (§6.1) — no test waits on wall-clock time, and a fake clock never has to reconstruct an absolute deadline the runtime already computed.
- «`publish` синхронный и принимает доменные объекты.» (§6.1) It projects and `put_nowait`s: no `await`, no blocking I/O, no exception escaping. A test enforces that, not the signature.
- «Origin разрешается **только после выхода из транзакции**.» (§6.2) Nothing externally visible happens while database locks are held.
- «Каждый origin разрешается ровно один раз … Разрешение не бросает и идемпотентно.» (§6.2)
- «`operation_id` **всегда генерируется сервером**, для любой команды без исключений.» (§5.1) This plan is where that generation lives.
- «Повтор переигрывает попытку целиком … стабилен только `operation_id`.» (§6.3)
- «Реконсиляция сравнивает саму пачку … Любое расхождение — карантин, никаких „почти совпало“.» (§6.3)
- «Отказ вещателя никогда не отправляет в карантин.» (§6.3)
- The event log stays append-only. The runtime writes through `TransactionContext.append` and nothing else.
- Code, identifiers and comments in English. No user-facing Russian copy appears in this plan.
- **Every test states what would kill it.** The test bodies below are specified by name and docstring rather than written out in full, which is a deliberate departure from this project's usual plan standard and the one place this plan asks for judgement instead of transcription. Each docstring says exactly what its test must prove; the implementer writes the body. To keep that from degrading into tests that pass for the wrong reason, every task's report must name, for each test it added, the single change to the code under test that would make it fail. A test whose killing mutation cannot be named is hollow and gets fixed before the task is reported.

## Rulings made while writing this plan

1. **`CategoryBank` is declared here and implemented in plan 6.** The materialiser genuinely needs it — `DealBoard` draws categories from the active library, `DeclareAttack` draws an image pack — so unlike plan 2's deferred ports this one has a consumer today. The protocol lives in `services/ports.py`, the runtime tests drive a fake, and plan 6 supplies the database-backed implementation. Nothing here reads a `categories` or `images` table, because neither exists yet.
2. **The runtime honours `deadline_id`; the domain still ignores it.** `decide` drops `ExpireTimer.deadline_id` on purpose — `Duel` does not record which `seq` set its anchor, so the domain has nothing to compare against, and §4.2 already makes a stale expiry harmless. §4.3 puts the identity check in the scheduler, which knows which task it cancelled. `MatchRuntime` therefore drops a mismatched `ExpireTimer` before `decide` sees it, and that drop is an *ignore*, not a rejection.
3. **Recovery's pause is an ordinary command decided at an extraordinary instant.** §4.4 wants a match whose duel was running to come back paused with the idle time charged to nobody. `PauseDuel` charges `elapsed_ms(anchor, now, remaining)`, so a pause decided at the moment of recovery would bill the whole outage to whoever happened to be answering — the exact failure §4.4 exists to prevent. Decided at the duel's own anchor, `elapsed_ms` returns zero and the remainders come back untouched. So `Materialiser.build` takes an optional `at` that overrides the clock reading, used in exactly one place: recovery. No new event type, no domain change, and the log records an ordinary `DuelPaused`.
4. **One queue per match, and the manager never runs two loops for one match.** §6: «на партию — одна последовательная очередь команд». The optimistic append would catch a second writer, but catching it is a failure path; not having one is the design.
5. **Quarantine is a terminal in-memory state, not a log entry.** A quarantined match stops consuming its queue, resolves every waiting origin with a failure, and refuses new commands until the process restarts. Nothing is written: the log is what we still trust.
6. **`api/` is not in this plan.** §7's REST and WebSocket layers are plan 4. `Broadcaster` and `Origin` are protocols with test fakes; the only production `Origin` here is the one the runtime issues to itself.
7. **The retry classifier reads SQLSTATE, not exception text.** §6.3 names `40001` and `40P01`. asyncpg surfaces those through SQLAlchemy's `DBAPIError.orig`; matching on message strings would silently stop working on a driver upgrade.

## File Structure

```
backend/src/podvinsya/services/__init__.py       create
backend/src/podvinsya/services/ports.py          create  every capability, as Protocol
backend/src/podvinsya/runtime/__init__.py        create
backend/src/podvinsya/runtime/errors.py          create  Quarantined and friends
backend/src/podvinsya/runtime/clock.py           create  SystemClock
backend/src/podvinsya/runtime/origins.py         create  CommandOutcome, SystemOrigin, FutureOrigin
backend/src/podvinsya/runtime/materialiser.py    create  a DecisionContext out of the world
backend/src/podvinsya/runtime/commit.py          create  one attempt, retries, reconciliation
backend/src/podvinsya/runtime/scheduler.py       create  the deadline task
backend/src/podvinsya/runtime/match.py           create  MatchRuntime: the queue and the cycle
backend/src/podvinsya/runtime/recovery.py        create  load, then pause what was running
backend/src/podvinsya/runtime/watchdog.py        create  the missing-deadline sweep
backend/src/podvinsya/runtime/manager.py         create  MatchManager: lifecycle
backend/tests/support/fakes.py                   create  clock, broadcaster, bank doubles
backend/tests/runtime/                           create  one module per task
```

---

### Task 1: The ports and the clock

Nothing runs yet. This task declares every capability the loop will need and supplies the one implementation that has no dependencies of its own, plus the fakes every later task's tests are built on.

**Files:**
- Create: `backend/src/podvinsya/services/__init__.py`, `backend/src/podvinsya/services/ports.py`
- Create: `backend/src/podvinsya/runtime/__init__.py`, `backend/src/podvinsya/runtime/clock.py`
- Create: `backend/tests/support/fakes.py`
- Test: `backend/tests/runtime/test_ports.py`, `backend/tests/runtime/test_clock.py`

**Interfaces:**
- Consumes: `podvinsya.domain` (state, events, ids, errors), and — as value types only — `podvinsya.db.store.Reconciliation` and `podvinsya.db.repository.LoadedMatch`.
- Produces: `Clock`, `Broadcaster`, `Origin`, `RuntimeCode`, `Transaction`, `UnitOfWorkPort`, `MatchRepositoryPort`, `CategoryBank` (all in `services.ports`); `SystemClock`; and the fakes `FakeClock`, `RecordingBroadcaster`, `FakeCategoryBank`.

**A ruling this task rests on.** `services/ports.py` imports two names from `db/`: `Reconciliation` (a three-valued `StrEnum`) and `LoadedMatch` (a frozen pair of a state and an int). Both are *data*, not capability, and importing them does not invert the dependency the layering rule guards against — no service code calls into `db` because of it. The alternative, re-declaring both under `services` and mapping between them at every boundary, buys purity on paper and costs a translation nobody would maintain. If a later plan wants the seam clean, moving those two declarations is a one-line change.

- [ ] **Step 1: Write the failing port-conformance test**

`backend/tests/runtime/test_ports.py`. Most of this test is checked by mypy, not at runtime: a `Protocol` is structural, so an assignment that type-checks *is* the proof that the concrete class satisfies it. `mypy --strict` failing is this test failing.

```python
"""The concrete classes satisfy the protocols — checked by mypy, not by
`isinstance`.

`runtime_checkable` would only verify that the method *names* exist; it says
nothing about their signatures, which is exactly where a port drifts from
its implementation. These assignments are the real assertion, and they fail
at type-check time rather than in a test run.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.runtime.clock import SystemClock
from podvinsya.services.ports import (
    Broadcaster,
    CategoryBank,
    Clock,
    MatchRepositoryPort,
    UnitOfWorkPort,
)
from support.fakes import FakeCategoryBank, FakeClock, RecordingBroadcaster


def test_the_persistence_classes_satisfy_their_ports(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """If `append`'s or `load`'s signature ever drifts from the port, this
    stops type-checking — which is the whole reason the port exists."""
    uow: UnitOfWorkPort = UnitOfWork(sessions)
    repository: MatchRepositoryPort = MatchRepository(sessions)
    assert uow is not None and repository is not None


def test_the_clock_and_the_fakes_satisfy_their_ports() -> None:
    system: Clock = SystemClock()
    fake: Clock = FakeClock(NOW)
    broadcaster: Broadcaster = RecordingBroadcaster()
    bank: CategoryBank = FakeCategoryBank()
    assert (system, fake, broadcaster, bank) is not None
```

`NOW` is a module-level `datetime(2026, 8, 23, 12, 0, tzinfo=UTC)`. This module needs the `sessions` fixture, so it lives under `tests/runtime/` with its own conftest — see Step 6.

- [ ] **Step 2: Write the failing clock test**

`backend/tests/runtime/test_clock.py`:

```python
"""The clock is the only place the runtime is allowed to learn the time."""

import time
from datetime import UTC, datetime, timedelta

import pytest

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
    not an error: the loop asks to sleep and gets control straight back.

    The elapsed-time assertion is what gives this test teeth. Without it,
    the sign-flip it exists to catch — `self.now() - when` — merely makes
    the call sleep five real seconds and pass anyway. Measuring that a call
    did *not* wait is not the same as waiting: the constraint forbids tests
    whose passing depends on time elapsing, and this asserts the opposite.
    """
    clock = SystemClock()
    started = time.monotonic()
    await clock.sleep_until(clock.now() - timedelta(seconds=5))
    assert time.monotonic() - started < 0.1


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
```

- [ ] **Step 3: Run both and watch them fail**

Run: `cd backend && .venv/bin/python -m pytest tests/runtime -v`
Expected: `ModuleNotFoundError` for `podvinsya.services.ports` and `support.fakes`.

- [ ] **Step 4: Write `services/ports.py`**

```python
"""Every capability the runtime needs, declared as a Protocol.

There are no implementations under `services/`. That is what keeps the
direction api → services → domain one-way, and what makes a test with a
fake clock, a breaking broadcaster and a breaking commit mechanical rather
than heroic.

Two names are imported from `db/`: `Reconciliation` and `LoadedMatch`. Both
are data — a three-valued enum and a frozen pair — not capability, and no
service code calls into `db` because they are here. See the plan's ruling.
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from podvinsya.db.repository import LoadedMatch
from podvinsya.db.store import Reconciliation
from podvinsya.domain.board import BoardSize
from podvinsya.domain.errors import RejectionReason
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import CategoryId, ImageId, MatchId, PlayerId
from podvinsya.domain.state import MatchState


class Clock(Protocol):
    """`sleep_until`, never `sleep`: no test may wait on wall-clock time,
    and a duration-based API would force a fake clock to reconstruct the
    absolute deadline the runtime has already computed."""

    def now(self) -> datetime: ...
    async def sleep_until(self, when: datetime) -> None: ...


class Broadcaster(Protocol):
    """Synchronous on purpose: the loop is forbidden to wait on a socket
    write, and a `def` cannot be accidentally awaited. It takes domain
    objects because only the WebSocket layer knows each subscriber's
    context, so the projection has to happen there.

    The contract a test enforces, which the signature cannot: project and
    `put_nowait`, nothing else. No await, no blocking I/O, no exception
    escaping.
    """

    def publish(
        self,
        match_id: MatchId,
        base_seq: int,
        state: MatchState,
        events: Sequence[Event],
    ) -> None: ...


class RuntimeCode(StrEnum):
    """Why a command did not succeed for a reason that is not the domain's.

    `CONTENT_UNAVAILABLE` is the odd one and deliberately so: §6.3 calls a
    content shortfall «обычный отказ, не авария», so it reaches the caller
    through this enum but never quarantines the match. The distinction the
    spec draws is about runtime health, not about which method the origin
    is told through.
    """

    QUARANTINED = "quarantined"
    DATABASE_UNAVAILABLE = "database_unavailable"
    CONTENT_UNAVAILABLE = "content_unavailable"
    INTERNAL = "internal"


class Origin(Protocol):
    """Whoever is waiting for a command's outcome.

    Resolution is exactly-once and idempotent, and it never raises. A REST
    client can disconnect while its command is queued, and `set_result` on
    a cancelled future would otherwise raise `InvalidStateError` *after*
    the commit — turning a delivery failure on a dead request into a
    quarantine for a match whose state is durable and correct.
    """

    def resolve_ok(self, events: Sequence[Event]) -> None: ...
    def resolve_noop(self) -> None: ...
    def resolve_rejected(self, reason: RejectionReason) -> None: ...
    def resolve_failed(self, code: RuntimeCode, message: str) -> None: ...


class Transaction(Protocol):
    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None: ...


class UnitOfWorkPort(Protocol):
    def begin(self) -> AbstractAsyncContextManager[Transaction]: ...

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation: ...


class MatchRepositoryPort(Protocol):
    async def create(
        self, match_id: MatchId, event: MatchCreated, *, operation_id: str
    ) -> None: ...

    async def read_events(self, match_id: MatchId) -> tuple[Event, ...]: ...

    async def load(self, match_id: MatchId) -> LoadedMatch: ...


class CategoryBank(Protocol):
    """The content library as the runtime needs it. Implemented in plan 6.

    It draws identifiers and nothing else: assembling a `DealPlan` — which
    cells go to whom, which group ids they carry, where each secret lands —
    is the materialiser's job, because those are rules (§3.4), not content.

    Every draw takes the transaction it must happen inside. §5.3 selects
    content under `FOR SHARE`, and those locks are released when the
    transaction ends — which is why §6.3 replays a retried attempt whole
    rather than reusing a pack drawn under locks that are already gone.
    """

    async def draw_categories(
        self, tx: "Transaction", count: int, *, exclude: frozenset[CategoryId]
    ) -> tuple[CategoryId, ...]: ...

    async def draw_images(
        self, tx: "Transaction", category: CategoryId, count: int
    ) -> tuple[ImageId, ...]: ...


class ContentExhausted(Exception):
    """The library cannot supply what was asked for.

    §8 calls image exhaustion a content defect rather than a domain
    transition, and §6.3 makes a content shortfall «обычный отказ, не
    авария» — so this is a rejection the operator sees, never a quarantine.
    """
```

`BoardSize` and `Mapping`/`PlayerId` are imported for the materialiser's use in Task 3; if ruff reports them unused at this point, leave them out and add them there.

- [ ] **Step 5: Write `runtime/clock.py`**

```python
"""The system clock: the only place production code learns the time."""

import asyncio
from datetime import UTC, datetime


class SystemClock:
    """Aware UTC, always. The codec refuses a naive datetime, and every
    instant that reaches an event comes from here."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
```

A deadline already in the past returns immediately rather than raising: after a slow commit that is the ordinary case, and the caller's next act is to check expiry anyway.

- [ ] **Step 6: Write `tests/support/fakes.py`**

```python
"""Test doubles for every port. The runtime suite is built on these.

`FakeClock` is the reason no test in this plan waits on wall-clock time: a
sleeper parks on an event, and the test decides when the moment arrives.
`settle` exists because a task that has been created has not necessarily
reached its `sleep_until` yet, and a test that advanced time before it did
would see a wake-up that never happened.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from podvinsya.domain.events import Event
from podvinsya.domain.ids import CategoryId, ImageId, MatchId
from podvinsya.domain.state import MatchState
from podvinsya.services.ports import ContentExhausted


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
```

Add the imports these need (`Sequence`, `uuid4`). `BreakingBroadcaster` is unused until Task 6 — if that bothers ruff, it will not, since it is a definition and not an import.

- [ ] **Step 7: Write `tests/runtime/conftest.py`**

The runtime suite needs the database fixtures for the tasks that talk to it, and needs the same loop-scope discipline `tests/db/` has. Rather than duplicate that conftest, import what it owns:

```python
"""Fixtures for the runtime suite.

The database-backed tests here reuse `tests/db`'s engine, session and
schema fixtures rather than building a second set; pytest resolves them
through the shared `tests/support/db.py` the same way. Modules that touch
the database carry `pytest.mark.integration`; modules that do not — the
clock and the ports, for instance — carry no mark and run in the fast lane.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from podvinsya.db.engine import create_engine, sessionmaker_for
from support.db import DATABASE_URL
```

Copy the `engine`, `migrated_schema`, `clean_db` and `sessions` fixtures from `tests/db/conftest.py` **by importing them**, not by retyping them — a second copy of that arrangement is a second thing to keep correct. If pytest will not resolve them across directories, the honest fix is to move them up into a shared `tests/conftest.py`; do that rather than duplicating, and say so in your report.

- [ ] **Step 8: Run the tests**

Run: `cd backend && .venv/bin/python -m pytest tests/runtime -v` — expected: all green.
Run: `.venv/bin/mypy` — this is where the port conformance is actually checked. If a concrete class does not satisfy its protocol, mypy says so here.
Run: `.venv/bin/ruff check .` — clean.
Run the whole suite: `.venv/bin/python -m pytest` — 290 existing plus the new ones.

- [ ] **Step 9: Commit**

```bash
git add backend/src/podvinsya/services backend/src/podvinsya/runtime backend/tests
git commit -m "Declare the runtime's ports and the clock behind them"
```

---

### Task 2: Origins and the command envelope

**Files:**
- Create: `backend/src/podvinsya/runtime/origins.py`
- Test: `backend/tests/runtime/test_origins.py`

**Interfaces:**
- Consumes: `Origin`, `RuntimeCode` (`services.ports`), `RejectionReason` (domain).
- Produces: `Accepted`, `NoOp`, `Rejected`, `Failed`, the `CommandOutcome` union, `SystemOrigin`, `FutureOrigin`, and `QueuedCommand` with its `issue` constructor.

- [ ] **Step 1: Write the failing tests**

`backend/tests/runtime/test_origins.py`:

```python
"""Resolution is exactly-once, idempotent, and never raises. Every one of
those three has cost somebody a production incident, so each gets a test."""

import asyncio

import pytest

from podvinsya.domain.actions import PauseDuel
from podvinsya.domain.errors import RejectionReason
from podvinsya.runtime.origins import (
    Accepted,
    Failed,
    FutureOrigin,
    NoOp,
    QueuedCommand,
    Rejected,
    SystemOrigin,
)
from podvinsya.services.ports import RuntimeCode


async def test_a_future_origin_hands_its_caller_the_events() -> None:
    origin = FutureOrigin()
    origin.resolve_ok(())
    assert await origin.result() == Accepted(())


async def test_each_resolution_maps_to_its_own_outcome() -> None:
    ok, noop, rejected, failed = (FutureOrigin() for _ in range(4))
    ok.resolve_ok(())
    noop.resolve_noop()
    rejected.resolve_rejected(RejectionReason.DUEL_PAUSED)
    failed.resolve_failed(RuntimeCode.QUARANTINED, "the match is quarantined")

    assert await ok.result() == Accepted(())
    assert await noop.result() == NoOp()
    assert await rejected.result() == Rejected(RejectionReason.DUEL_PAUSED)
    assert (await failed.result()) == Failed(RuntimeCode.QUARANTINED, "the match is quarantined")


async def test_a_second_resolution_is_ignored_rather_than_raising() -> None:
    """The loop resolves on exactly one path, but a bug that resolved twice
    must not take the match down after its commit already landed."""
    origin = FutureOrigin()
    origin.resolve_ok(())
    origin.resolve_failed(RuntimeCode.INTERNAL, "should never be seen")
    assert await origin.result() == Accepted(())


async def test_resolving_an_abandoned_caller_does_not_raise() -> None:
    """A REST client can disconnect while its command is queued. Setting a
    result on its cancelled future raises InvalidStateError — *after* the
    commit. If that escaped, a delivery failure on a dead request would
    quarantine a match whose state is durable and correct."""
    origin = FutureOrigin()
    origin.abandon()
    origin.resolve_ok(())  # must not raise


async def test_a_system_origin_absorbs_every_outcome() -> None:
    """Nobody is waiting on a deadline expiry or a recovery pause, but the
    loop still resolves unconditionally — a nullable origin would put a
    branch on every resolution path, and the forgotten one would hang."""
    origin = SystemOrigin("deadline")
    origin.resolve_ok(())
    origin.resolve_noop()
    origin.resolve_rejected(RejectionReason.NO_DUEL)
    origin.resolve_failed(RuntimeCode.INTERNAL, "boom")


async def test_a_system_origin_logs_a_rejection_it_should_never_get(
    caplog: pytest.LogCaptureFixture
) -> None:
    """A server-issued command being rejected means the server's own model
    of the match was wrong. Silence there is how that stays undiscovered."""
    with caplog.at_level("WARNING"):
        SystemOrigin("watchdog").resolve_rejected(RejectionReason.NO_DUEL)
    assert "watchdog" in caplog.text
    assert RejectionReason.NO_DUEL.value in caplog.text


def test_the_server_mints_the_operation_id() -> None:
    """§5.1: always, for every command without exception. A client-supplied
    value is untrusted input — repeating someone else's would make the
    reconciliation of an ambiguous commit conclude the wrong batch landed."""
    first = QueuedCommand.issue(PauseDuel(), SystemOrigin("test"))
    second = QueuedCommand.issue(PauseDuel(), SystemOrigin("test"))
    assert first.operation_id != second.operation_id
    assert first.operation_id


def test_the_envelope_carries_the_command_and_its_origin_unchanged() -> None:
    command = PauseDuel()
    origin = SystemOrigin("test")
    queued = QueuedCommand.issue(command, origin)
    assert queued.command is command
    assert queued.origin is origin
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/runtime/test_origins.py -v`
Expected: `ModuleNotFoundError: No module named 'podvinsya.runtime.origins'`.

- [ ] **Step 3: Write `runtime/origins.py`**

```python
"""Who is waiting for a command's outcome, and how they are told.

Two implementations cover everything. `FutureOrigin` serves a caller that
awaits a result — plan 4's REST replies and WebSocket acknowledgements.
`SystemOrigin` serves commands the server issues to itself: deadline
expiries, watchdog re-arms, the recovery pause. Nobody waits on those, but
the loop still resolves them unconditionally: a nullable origin would put a
branch on every resolution path in the loop, and the one that got forgotten
would be a hung request.
"""

import asyncio
import logging
from dataclasses import dataclass
from collections.abc import Sequence
from uuid import uuid4

from podvinsya.domain.actions import Command
from podvinsya.domain.errors import RejectionReason
from podvinsya.domain.events import Event
from podvinsya.services.ports import Origin, RuntimeCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Accepted:
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class NoOp:
    """`decide` produced nothing. Nothing was persisted and nothing is
    broadcast — and this is deliberately not an error: a stale expiry or a
    duplicate is a benign race, not a fault."""


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class Failed:
    code: RuntimeCode
    message: str


CommandOutcome = Accepted | NoOp | Rejected | Failed


class SystemOrigin:
    """A command the server issued to itself. `label` names the issuer, so
    a rejection that should never happen is greppable."""

    def __init__(self, label: str) -> None:
        self._label = label

    def resolve_ok(self, events: Sequence[Event]) -> None:
        return None

    def resolve_noop(self) -> None:
        return None

    def resolve_rejected(self, reason: RejectionReason) -> None:
        # A server-issued command being rejected means the server's own
        # model of the match disagreed with the domain's. That is a bug
        # worth finding, and silence is how it stays unfound.
        logger.warning("%s command rejected: %s", self._label, reason.value)

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        logger.warning("%s command failed: %s — %s", self._label, code.value, message)


class FutureOrigin:
    """A caller awaiting an outcome.

    Every resolution path funnels through `_settle`, which is what makes
    the exactly-once and never-raises guarantees a property of one place
    rather than of four.
    """

    def __init__(self) -> None:
        self._future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()

    async def result(self) -> CommandOutcome:
        return await self._future

    def abandon(self) -> None:
        """The caller went away. Used by plan 4 when a request is dropped
        before its command has been consumed."""
        self._future.cancel()

    def _settle(self, outcome: CommandOutcome) -> None:
        if self._future.done():
            # Already resolved, or the caller abandoned it. Either way the
            # command's real outcome is durable and this delivery is not
            # worth an exception on the loop's own thread.
            return
        try:
            self._future.set_result(outcome)
        except asyncio.InvalidStateError:  # pragma: no cover - raced cancellation
            logger.info("origin abandoned before its outcome could be delivered")

    def resolve_ok(self, events: Sequence[Event]) -> None:
        self._settle(Accepted(tuple(events)))

    def resolve_noop(self) -> None:
        self._settle(NoOp())

    def resolve_rejected(self, reason: RejectionReason) -> None:
        self._settle(Rejected(reason))

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self._settle(Failed(code, message))


@dataclass(frozen=True, slots=True)
class QueuedCommand:
    """One command, its server-minted identity, and whoever is waiting."""

    command: Command
    operation_id: str
    origin: Origin

    @classmethod
    def issue(cls, command: Command, origin: Origin) -> "QueuedCommand":
        """Mint the `operation_id` here and nowhere else.

        §5.1 makes this the server's job for every command without
        exception — WebSocket, REST, timer, watchdog. A client-supplied
        value is untrusted input: repeating someone else's would make the
        reconciliation of an ambiguous commit conclude that the batch it
        was looking for had already been written.
        """
        return cls(command=command, operation_id=str(uuid4()), origin=origin)
```

- [ ] **Step 4: Run, type-check, lint, commit**

Run: `.venv/bin/python -m pytest tests/runtime -v`, then `.venv/bin/mypy` and `.venv/bin/ruff check .`.

```bash
git add backend/src/podvinsya/runtime/origins.py backend/tests/runtime/test_origins.py
git commit -m "Tell exactly one waiter, exactly once, without ever raising"
```

---

### Task 3: The materialiser

Every non-deterministic input the domain needs arrives as a value on a `DecisionContext`. This task is what produces those values: the clock reading, the deal, the image pack, and the duel journal. It is the single most consequential task in this plan, because the rule that **undo never crosses into the previous duel** is enforced here and nowhere else — the domain reads the journal it is handed and trusts it.

**Files:**
- Create: `backend/src/podvinsya/runtime/materialiser.py`
- Test: `backend/tests/runtime/test_materialiser.py`

**Interfaces:**
- Consumes: `Clock`, `MatchRepositoryPort`, `CategoryBank`, `Transaction`, `ContentExhausted` (`services.ports`); `create_initial_state`, `evolve`, `DecisionContext`, `DealPlan`, `DealtCell`, `JournalEntry` (domain).
- Produces: `Materialiser(clock, repository, bank, random)` with `async def build(state, command, tx) -> DecisionContext`, and the constant `IMAGE_PACK_SIZE`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/runtime/test_materialiser.py`. This module is integration-marked: the journal is derived from the real log.

```python
"""The materialiser hands the domain every value it cannot compute itself.

The journal tests are the ones that matter. Plan 1 left a carry-forward in
so many words: «undo never crosses into the previous duel» is enforced by
whoever assembles `ctx.duel_journal`, not by the domain — the domain reads
what it is given. This is that assembler.
"""

from datetime import UTC, datetime
from random import Random

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.actions import DealBoard, DeclareAttack, PauseDuel, UndoLastJudgement
from podvinsya.domain.decide import decide
from podvinsya.domain.evolve import fold
from podvinsya.domain.events import AnswerAccepted, DuelStarted, PassUsed
from podvinsya.domain.state import MatchStatus
from podvinsya.runtime.materialiser import IMAGE_PACK_SIZE, Materialiser
from podvinsya.services.ports import ContentExhausted
from support.fakes import FakeCategoryBank, FakeClock
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


async def test_every_context_carries_the_clock_reading(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The domain never reads a clock. This is the only place `now` enters."""
    materialiser = _materialiser(sessions)
    state = build_rich_stream().state
    ctx = await materialiser.build(state, PauseDuel(), _tx())
    assert ctx.now == NOW


async def test_a_plain_command_gets_nothing_it_does_not_need(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A context carrying a deal for a `PauseDuel` would mean the deal was
    drawn — and content drawn under a transaction's locks is not free."""
    ctx = await _materialiser(sessions).build(build_rich_stream().state, PauseDuel(), _tx())
    assert ctx.deal is None
    assert ctx.image_order is None
    assert ctx.duel_journal == ()


async def test_the_journal_holds_one_entry_per_judgement_of_this_duel(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """One `AnswerAccepted` and one `PassUsed` were judged in the rich
    stream's first duel before the undo, so the journal handed to that undo
    must hold exactly those two, oldest first."""
    ...


async def test_the_journal_never_reaches_into_the_previous_duel(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The carry-forward from plan 1, and the reason this task exists.

    A stream with judgements in duel one and judgements in duel two must
    hand duel two's undo only duel two's entries. If this fails, an
    operator's undo can reach back across a resolved duel and rewrite an
    ownership transfer the rules call final.

    The assertion is on `seq`: every entry the journal returns must have
    been recorded after the current duel's `DuelStarted`. Asserting only on
    the count would pass if the journal held one entry from each duel.
    """
    recorded = await _persist_two_judged_duels(sessions)
    materialiser = _materialiser(sessions)

    journal = (
        await materialiser.build(recorded.state, UndoLastJudgement(), _tx())
    ).duel_journal

    last_duel_started = max(
        index for index, event in enumerate(recorded.events)
        if isinstance(event, DuelStarted)
    )
    boundary = last_duel_started + 1  # seq is one-based over the same list
    assert journal, "the second duel had judgements; the journal cannot be empty"
    assert all(entry.seq >= boundary for entry in journal), (
        "an entry from before this duel started would let an undo rewrite a "
        f"resolved duel: {[entry.seq for entry in journal]} against {boundary}"
    )


async def test_the_journal_survives_a_resolved_duel_in_between(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The boundary is `DuelStarted`, not "the last few events".

    A duel that resolved between the two judged ones puts `DuelResolved`,
    `AttackDeclared` and `DuelStarted` in the log in that order. A journal
    built by scanning backwards for judgements without stopping at
    `DuelStarted` would sail straight through all three.
    """
    recorded = await _persist_two_judged_duels(sessions)
    journal = (
        await _materialiser(sessions).build(recorded.state, UndoLastJudgement(), _tx())
    ).duel_journal
    judgements_in_the_log = sum(
        1 for event in recorded.events if isinstance(event, AnswerAccepted | PassUsed)
    )
    assert len(journal) < judgements_in_the_log, (
        "the journal holds every judgement in the match, so the duel "
        "boundary is not being honoured at all"
    )


async def test_an_undone_judgement_leaves_the_journal(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A `JudgementUndone` in the log means its entry was already consumed.
    Leaving it would let two undos rewind one judgement — the chain would
    stop being a chain."""
    ...


async def test_the_journal_is_empty_for_a_duel_nobody_has_judged(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The domain rejects `UndoLastJudgement` with NOTHING_TO_UNDO when the
    journal is empty. That rejection is only correct if the emptiness is."""
    ...


async def test_a_declared_attack_draws_a_pack_for_the_defenders_category(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The duel is played on the defender's category (§2.6), and the draw
    happens at declaration, not at start — otherwise the warm-up window is
    fictional and the stage screen gets its preload and its live deadline
    in the same frame (§3.5)."""
    ...


async def test_a_deal_gives_every_player_an_equal_share(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ...


async def test_a_deal_puts_each_secret_on_a_cell_its_owner_holds(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ...


async def test_a_deal_gives_every_cell_a_category_of_its_own(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ...


async def test_the_deal_the_materialiser_builds_is_one_the_domain_accepts(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The domain validates a deal ten different ways. Rather than assert
    those ten properties again here, hand the plan to `decide` and let the
    real validator judge it — the one that will judge it in production."""
    ...


async def test_a_library_too_small_to_deal_is_a_rejection_not_a_crash(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.3: «нехватка контента при отборе — обычный отказ, не авария»."""
    ...


async def test_two_deals_from_the_same_state_differ(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """«Раздача случайна и вразброс» (§3.4), and re-dealing is the host's
    shuffle button. A deterministic deal would make that button a no-op."""
    ...
```

**Write these tests out in full.** The bodies are elided here only because each needs identifiers the fixtures mint; the docstrings state exactly what each must prove, and every one of them must fail if the behaviour it names is removed. In particular:

- `_persist_two_judged_duels` is yours to write: a helper that drives `decide`/`fold` to produce a match with judgements in its first duel, that duel resolved, and judgements in a second, then persists the whole stream through `MatchRepository.create` and `TransactionContext.append` and returns the `Recorded`. `build_rich_stream()` hand-plays only its first duel, so extend it in the helper rather than in `support/streams.py` — and if you do end up adding a second hand-played duel there, `test_the_stream_has_the_expected_shape` must be updated deliberately, not silently.
- `_materialiser(sessions)` builds a `Materialiser(FakeClock(NOW), MatchRepository(sessions), FakeCategoryBank(), Random(0))`. `_tx()` supplies a transaction to the bank; since `FakeCategoryBank` ignores it, a trivial stand-in is honest here — but say in your report what you passed, and if you find yourself needing a real one, open a transaction rather than faking it.
- `test_the_deal_the_materialiser_builds_is_one_the_domain_accepts` is the strongest of the deal tests: build a match in SETUP, materialise a `DealBoard` context, and call `decide(state, DealBoard(), ctx)`. If it raises `Rejected`, the materialiser is producing a deal the game would refuse.

- [ ] **Step 2: Run and watch it fail**

Expected: `ModuleNotFoundError: No module named 'podvinsya.runtime.materialiser'`.

- [ ] **Step 3: Write `runtime/materialiser.py`**

```python
"""Every non-deterministic input the domain needs, resolved into a value.

`decide` is pure: it reads no clock, draws no random number, and queries
nothing. That is what makes it testable, and it holds only because this
module does all of it first and hands the results over as data.

Three of the four inputs are drawn only for the command that needs them. A
context carrying a deal for a `PauseDuel` would mean content was selected —
under transaction locks — for a command that will never look at it.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from random import Random
from uuid import uuid4

from podvinsya.domain.actions import Command, DealBoard, DeclareAttack, UndoLastJudgement
from podvinsya.domain.board import Cell
from podvinsya.domain.context import DealPlan, DealtCell, DecisionContext, JournalEntry
from podvinsya.domain.events import (
    AnswerAccepted,
    DuelStarted,
    Event,
    JudgementUndone,
    PassUsed,
)
from podvinsya.domain.evolve import evolve
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, GroupId, PlayerId
from podvinsya.domain.state import MatchState
from podvinsya.services.ports import (
    CategoryBank,
    Clock,
    MatchRepositoryPort,
    Transaction,
)

logger = logging.getLogger(__name__)

# One pack is drawn per duel and never extended (§3.5). A duel is bounded by
# the two budgets — sixty seconds plus at most fifteen of bonus each — and a
# host cannot judge an image in much under a second of speech, so sixty is
# comfortably more than a duel can consume. Running past the end is a content
# defect, not a domain transition (§8), and it surfaces as a rejection.
IMAGE_PACK_SIZE = 60


class Materialiser:
    def __init__(
        self,
        clock: Clock,
        repository: MatchRepositoryPort,
        bank: CategoryBank,
        random: Random,
    ) -> None:
        self._clock = clock
        self._repository = repository
        self._bank = bank
        self._random = random

    async def build(
        self,
        state: MatchState,
        command: Command,
        tx: Transaction,
        *,
        at: datetime | None = None,
    ) -> DecisionContext:
        """`at` overrides the clock reading and exists for exactly one
        caller: recovery (§4.4), which pauses a duel as of its own anchor so
        the outage is charged to nobody. Everywhere else the clock decides,
        and passing `at` from anywhere else is a bug worth reviewing."""
        now = at if at is not None else self._clock.now()
        match command:
            case DealBoard():
                return DecisionContext(now=now, deal=await self._deal(state, tx))
            case DeclareAttack():
                return DecisionContext(
                    now=now, image_order=await self._images(state, command, tx)
                )
            case UndoLastJudgement():
                return DecisionContext(now=now, duel_journal=await self._journal(state))
            case _:
                return DecisionContext(now=now)

    async def _journal(self, state: MatchState) -> tuple[JournalEntry, ...]:
        """Rebuild the current duel's judging history from the log.

        The domain reads this and trusts it, so the rule that undo cannot
        reach past the start of the current duel lives here: `DuelStarted`
        clears the journal. Without that line, an undo could rewind a
        judgement from a duel that has already resolved and transferred
        ownership — a transfer §3.7 calls final.

        A `JudgementUndone` in the log pops the entry it consumed, so a
        chain of undos walks backwards one judgement at a time instead of
        rewinding the same one twice.

        The whole log is replayed rather than kept in memory: a match is a
        few dozen events, this runs only for `UndoLastJudgement`, and a
        cached journal would be one more thing that can disagree with the
        log after a recovery.
        """
        events = await self._repository.read_events(state.id)
        replay = create_initial_state(state.id, state.board, state.settings)
        journal: list[JournalEntry] = []
        for event in events:
            match event:
                case DuelStarted():
                    journal.clear()
                case AnswerAccepted() | PassUsed():
                    journal.append(_snapshot(replay))
                case JudgementUndone():
                    if journal:
                        journal.pop()
                case _:
                    pass
            replay = evolve(replay, event)
        return tuple(journal)

    async def _images(
        self, state: MatchState, command: DeclareAttack, tx: Transaction
    ) -> tuple[ImageId, ...]:
        """Draw the whole pack at declaration (§3.5).

        The defender's category is the one played (§2.6), and the order is
        settled once and written into the event whole, so the stage screen
        can preload every image while the host explains the category.
        """
        defending = state.groups[command.defending_group]
        return await self._bank.draw_images(tx, defending.category, IMAGE_PACK_SIZE)

    async def _deal(self, state: MatchState, tx: Transaction) -> DealPlan:
        """§3.4, in order: cells split evenly and at random, a distinct
        category on every cell, each player's secret on one of their own
        cells, and the ordinary categories drawn from the active library
        without repeats.

        A re-deal is the host's shuffle button, so this must genuinely
        differ from run to run — the randomness is injected rather than
        global so a test can pin it without the production path being
        deterministic.
        """
        players = tuple(player.id for player in state.players)
        secrets = dict(state.secrets)
        cells = list(state.board.cells())
        self._random.shuffle(cells)

        per_player = len(cells) // len(players)
        ordinary = await self._bank.draw_categories(
            tx, len(cells) - len(players), exclude=frozenset(secrets.values())
        )

        dealt: list[DealtCell] = []
        pool = iter(ordinary)
        for index, owner in enumerate(players):
            owned = cells[index * per_player : (index + 1) * per_player]
            for position, cell in enumerate(owned):
                is_secret = position == 0  # the shuffle already made this random
                dealt.append(
                    DealtCell(
                        cell=cell,
                        owner=owner,
                        category=secrets[owner] if is_secret else next(pool),
                        group_id=GroupId(uuid4()),
                        revealed=not is_secret,
                    )
                )
        return DealPlan(cells=tuple(dealt))


def _snapshot(state: MatchState) -> JournalEntry:
    """The duel as it stood immediately before one judging event.

    `budgets`, `answering` and `image_index` describe the state *before* the
    judgement — that is what an undo restores. `seq` is the exception: it
    identifies the judgement itself, because `decide` writes it through as
    `JudgementUndone.undone_seq`, and an event's persisted seq is its
    one-based position in the log. This state has not been folded with that
    event yet, so its own seq is one short of it.
    """
    duel = state.duel
    assert duel is not None, "a judging event outside a duel is a corrupt log"
    return JournalEntry(
        seq=state.seq + 1,
        budgets=duel.budgets,
        answering=duel.answering,
        image_index=duel.index,
    )
```

Add the `ImageId` import. Note that `draw_categories` and `draw_images` take the transaction as their first argument — the selection has to happen inside it, because §5.3's `FOR SHARE` locks are released when it ends, and §6.3 replays a retried attempt whole for exactly that reason.

- [ ] **Step 4: Run the tests, then check the whole suite**

Run: `.venv/bin/python -m pytest tests/runtime -v`, then the full suite, `.venv/bin/mypy` and `.venv/bin/ruff check .`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/podvinsya/runtime/materialiser.py backend/tests/runtime/test_materialiser.py
git commit -m "Resolve every non-deterministic input into a value"
```

---

### Task 4: One attempt, its retries, and its reconciliation

This is §6.3's failure-policy table made executable. Everything above it — the queue, the scheduler, the manager — assumes that a command either landed in the log or provably did not, and this is what decides which.

**Files:**
- Create: `backend/src/podvinsya/runtime/errors.py`, `backend/src/podvinsya/runtime/commit.py`
- Test: `backend/tests/runtime/test_commit.py`

**Interfaces:**
- Consumes: `UnitOfWorkPort`, `Transaction`, `Reconciliation`, `RuntimeCode`, `ContentExhausted`; `Materialiser`; `QueuedCommand`; `decide` and `Rejected` from the domain; `ConcurrentModification` from `db.errors`.
- Produces: `CommitPath(uow, materialiser, clock, random, *, max_attempts=3)` with `async def run(state, queued) -> CommandOutcome`, and the private `_Retry` sentinel.

**One union, not two.** An earlier draft of this plan gave the commit path its own `Committed`/`NothingToDo`/`Refused`/`Broken` beside Task 2's `Accepted`/`NoOp`/`Rejected`/`Failed` — four pairs with identical shapes and different names, plus a translation in the cycle that could only ever be a bug factory. `run` returns Task 2's `CommandOutcome` directly. `_Retry` is private to this module and never escapes `run`.

**The table this implements**, from §6.3, verbatim in the left column:

| Условие | What `run` returns |
|---|---|
| отказ из `decide` | `Rejected(reason)` — state untouched, runtime healthy |
| нехватка контента при отборе | `Failed(CONTENT_UNAVAILABLE, …)` — an ordinary refusal; **no quarantine** |
| известный откат БД (`40001`, `40P01`) | retry, bounded, with jitter, re-materialising and re-deciding in a fresh transaction |
| неоднозначный коммит | reconcile on `(match_id, operation_id)` |
| БД недоступна после повторов | `Failed(DATABASE_UNAVAILABLE, …)` — the caller quarantines |
| исключение в `decide` / `evolve` | `Failed(INTERNAL, …)`, no retries |
| исключение в материализаторе | `Failed(INTERNAL, …)` |

The broadcaster row is not here: it happens after the commit, and it belongs to Task 6.

- [ ] **Step 1: Write the failing tests**

`backend/tests/runtime/test_commit.py`. These drive fakes, not PostgreSQL — the point is the classification, and a real database will not produce a serialisation failure on demand.

```python
"""§6.3's table, one test per row.

Every fake here fails in a specific, named way. A single "the database
broke" fake would let three different rows of the table collapse into one
untested branch.
"""

...

async def test_a_rejection_from_decide_leaves_the_runtime_healthy() -> None:
    """«отказ из decide → откат, ответ origin, состояние не тронуто,
    рантайм здоров». The transaction must have rolled back and nothing
    may have been appended."""


async def test_a_content_shortfall_is_an_ordinary_refusal() -> None:
    """«нехватка контента при отборе → обычный отказ, не авария». The
    outcome names CONTENT_UNAVAILABLE and the caller is told; the match is
    not quarantined."""


async def test_a_serialisation_failure_is_retried() -> None:
    """SQLSTATE 40001. The second attempt must succeed and the outcome
    must be a plain commit — the caller never learns there was a retry."""


async def test_a_deadlock_is_retried() -> None:
    """SQLSTATE 40P01, the other half of «известный откат БД»."""


async def test_a_retry_re_materialises_and_re_decides() -> None:
    """«Повтор переигрывает попытку целиком … переиспользование уже
    материализованного тиража означало бы выбор под блокировками, которых
    больше нет.» Assert the materialiser was called once per attempt, not
    once for the command."""


async def test_a_retry_keeps_the_operation_id_and_nothing_else() -> None:
    """«стабилен только operation_id». If the retry minted a new one,
    reconciliation after an ambiguous commit would look for a batch that
    was never written under that name."""


async def test_a_retry_that_legitimately_decides_differently_is_accepted() -> None:
    """«Переигрывание может законно дать другие события — это корректно,
    потому что ничего не было закоммичено.» A re-deal draws a different
    shuffle; the attempt must not compare the two and panic."""


async def test_retries_are_bounded_and_then_the_database_is_unavailable() -> None:
    """«БД недоступна после повторов → карантин». The outcome says
    DATABASE_UNAVAILABLE; quarantining is the caller's move, not this
    function's."""


async def test_an_error_out_of_the_commit_itself_reconciles() -> None:
    """An error raised while the body was still running rolled back and is
    unambiguous. One raised as the transaction closes is not: the COMMIT
    may have reached the server. That is the case that reconciles."""


async def test_a_reconciled_match_is_reported_as_committed() -> None:
    """«Совпало — коммит прошёл, обработка продолжается со свёртки.»"""


async def test_a_reconciled_absence_is_retried() -> None:
    """ABSENT means the batch never landed, so the attempt is replayed —
    collapsing it into a divergence would quarantine a healthy match."""


async def test_a_reconciled_divergence_breaks_the_match() -> None:
    """«Любое расхождение — карантин, никаких „почти совпало“.»"""


async def test_an_exception_in_decide_is_not_retried() -> None:
    """«исключение в decide / evolve → карантин, без повторов». A bug in
    the domain reproduces exactly on replay; retrying it three times just
    delays the diagnosis."""


async def test_an_exception_in_the_materialiser_breaks_the_match() -> None:
    """«исключение в материализаторе → карантин»."""


async def test_a_concurrent_modification_breaks_the_match() -> None:
    """Someone else advanced last_seq past what this attempt's decide saw.
    §6.3 has no retry row for it, and retrying would append events decided
    against state that is no longer current."""


async def test_no_events_means_nothing_is_appended() -> None:
    """§6.2 resolves a no-op without opening the log at all."""


async def test_the_retry_delay_is_taken_from_the_clock() -> None:
    """No test may wait on the wall. The backoff sleeps through the clock
    port, so a fake clock makes the delay a value the test controls."""
```

Write these out in full. The fakes each need to fail one specific way: a `UnitOfWork` whose `begin` body raises a chosen `DBAPIError`, one whose `__aexit__` raises (the ambiguous commit), one whose `reconcile` returns each of the three outcomes, and a materialiser stub that raises. Build them in the test module rather than in `support/fakes.py` — they exist to exercise one function and nowhere else needs them.

To build a `DBAPIError` carrying a SQLSTATE without a live database, construct one whose `orig` exposes `sqlstate` — that is what the classifier reads. Confirm by experiment which attribute asyncpg actually populates (`sqlstate` and `pgcode` are both plausible) and make the classifier read what is really there, not what looks right.

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Write `runtime/errors.py`**

```python
"""Runtime-level failures.

`Quarantined` is a state, not really an error: a match that reaches it
stops consuming its queue and refuses new commands until the process
restarts. Nothing is written to the log when it happens — the log is what
we still trust.
"""


class Quarantined(Exception):
    """This match is no longer being played by this process."""


class MatchAlreadyRunning(Exception):
    """The manager was asked to start a second loop for one match.

    §6 gives each match one sequential queue. The optimistic append would
    catch a second writer, but catching it is a failure path; not having
    one is the design.
    """
```

- [ ] **Step 4: Write `runtime/commit.py`**

The shape, with the parts that matter spelled out:

```python
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})  # serialisation failure, deadlock


def _sqlstate(error: DBAPIError) -> str | None:
    """Read the SQLSTATE off the driver's own exception.

    Matching on message text would work today and stop working silently on
    the next driver upgrade, which is exactly the kind of breakage nobody
    notices until a retry storm.
    """
    return getattr(error.orig, "sqlstate", None)


class CommitPath:
    async def run(self, state: MatchState, queued: QueuedCommand) -> CommandOutcome:
        for attempt in range(self._max_attempts):
            outcome = await self._attempt(state, queued)
            if not isinstance(outcome, _Retry):
                return outcome
            await self._backoff(attempt)
        return Failed(RuntimeCode.DATABASE_UNAVAILABLE, "retries exhausted")

    async def _attempt(
        self, state: MatchState, queued: QueuedCommand
    ) -> CommandOutcome | _Retry:
        events: tuple[Event, ...] = ()
        body_completed = False
        try:
            async with self._uow.begin() as tx:
                ctx = await self._materialiser.build(state, queued.command, tx)
                events = decide(state, queued.command, ctx)
                if not events:
                    return NothingToDo()
                await tx.append(
                    state.id,
                    expected_last_seq=state.seq,
                    events=events,
                    operation_id=queued.operation_id,
                )
                body_completed = True
        except Rejected as refusal:
            return Rejected(refusal.reason)
        except ContentExhausted as shortfall:
            return Failed(RuntimeCode.CONTENT_UNAVAILABLE, str(shortfall))
        except ConcurrentModification:
            return Failed(RuntimeCode.INTERNAL, "another writer advanced this match")
        except DBAPIError as error:
            return await self._after_database_error(error, state, queued, events, body_completed)
        except Exception as unexpected:
            # decide, evolve or the materialiser blew up. §6.3 gives all
            # three the same answer and explicitly no retries: a bug
            # reproduces exactly on replay.
            logger.exception("attempt failed for %s", state.id)
            return Failed(RuntimeCode.INTERNAL, repr(unexpected))
        return Committed(events)
```

`_after_database_error` is where the ambiguity lives:

```python
    async def _after_database_error(self, error, state, queued, events, body_completed):
        """A body that never finished rolled back, and the failure is
        unambiguous — retry it if the SQLSTATE says so. A body that finished
        means the COMMIT was already in flight, and whether it landed is
        exactly what reconciliation answers.
        """
        if not body_completed:
            if _sqlstate(error) in RETRYABLE_SQLSTATES:
                return _Retry()
            return Failed(RuntimeCode.DATABASE_UNAVAILABLE, repr(error))

        outcome = await self._uow.reconcile(
            state.id,
            queued.operation_id,
            expected_last_seq=state.seq,
            events=events,
        )
        match outcome:
            case Reconciliation.MATCHED:
                return Accepted(events)
            case Reconciliation.ABSENT:
                return _Retry()
            case Reconciliation.DIVERGED:
                return Failed(RuntimeCode.INTERNAL, "the log диverged from this batch")
```

Fix that last string — it is deliberately mangled here so nobody pastes it without reading. Write it in English.

`_Retry` is a private sentinel dataclass, never returned to a caller. `_backoff` sleeps through the clock:

```python
    async def _backoff(self, attempt: int) -> None:
        delay_ms = self._base_delay_ms * (2**attempt)
        jitter = self._random.uniform(0.5, 1.5)
        await self._clock.sleep_until(
            self._clock.now() + timedelta(milliseconds=delay_ms * jitter)
        )
```

- [ ] **Step 5: Run, type-check, lint, commit**

```bash
git add backend/src/podvinsya/runtime backend/tests/runtime/test_commit.py
git commit -m "Classify every way one attempt can end"
```

---

### Task 5: The deadline scheduler

**Files:**
- Create: `backend/src/podvinsya/runtime/scheduler.py`
- Test: `backend/tests/runtime/test_scheduler.py`

**Interfaces:**
- Consumes: `Clock`; `deadline_of` (domain `timing`); `MatchState`.
- Produces: `DeadlineScheduler(clock, fire)` with `reschedule(state) -> None`, `cancel() -> None`, and `scheduled_for` / `deadline_id` accessors for the watchdog.

**What §4.3 requires.** A one-shot `asyncio.Task`, cancelled and recreated whenever `deadline_id` changes. The identifier is the `seq` of the event that set the anchor. An `ExpireTimer` whose identifier does not match is ignored, so correctness never depends on whether the cancellation won its race. A pause is a legitimate "no deadline" state: the task is cancelled and not recreated. The sleep goes through `clock.sleep_until`, never `sleep`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_running_duel_gets_a_task_that_fires_at_its_deadline() -> None:
    """The deadline is derived, never stored: anchor plus the answering
    player's remaining time."""


async def test_nothing_is_scheduled_for_a_paused_duel() -> None:
    """«Пауза — легальное состояние „дедлайна нет“»: the task is cancelled
    and not recreated."""


async def test_nothing_is_scheduled_when_there_is_no_duel() -> None:


async def test_rescheduling_to_the_same_deadline_id_keeps_the_task() -> None:
    """A re-publish or a redundant call must not churn the task — every
    cancel/recreate is a window where the watchdog sees no deadline."""


async def test_a_new_deadline_id_replaces_the_task() -> None:


async def test_the_replaced_task_does_not_fire() -> None:
    """The point of the identifier is that a stale task is harmless, but a
    stale task that still fires wastes a queue slot on every judgement."""


async def test_the_scheduler_fires_with_the_deadline_id_it_slept_on() -> None:
    """`ExpireTimer(deadline_id)` carries it so the runtime can drop a
    stale one. Firing with the current id instead would make the check
    tautological."""


async def test_cancelling_twice_is_harmless() -> None:
    """Shutdown calls it, and so does a pause that arrives first."""


async def test_the_scheduler_never_sleeps_on_the_wall_clock() -> None:
    """Every wait in this module goes through the clock port. Drive the
    fake clock past the deadline and the task must fire; leave it alone and
    the task must not."""
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Write `runtime/scheduler.py`**

```python
"""One task, one deadline, and an identifier that makes staleness harmless.

The identifier is the `seq` of the event that set the current anchor. A
timer that fires carrying an identifier the runtime has moved past is
dropped, so nothing here depends on a cancellation winning its race — which
is the only way to make this correct without a lock the loop cannot hold.
"""

Callback = Callable[[int], Awaitable[None]]


class DeadlineScheduler:
    def __init__(self, clock: Clock, fire: Callback) -> None:
        self._clock = clock
        self._fire = fire
        self._task: asyncio.Task[None] | None = None
        self._deadline_id: int | None = None
        self._scheduled_for: datetime | None = None

    def reschedule(self, state: MatchState) -> None:
        """Point the task at whatever deadline this state implies.

        A state with no duel, or a paused one, means no deadline: cancel
        and do not recreate. A state whose deadline identifier is the one
        already scheduled means leave the task alone — churning it opens a
        window where the watchdog sees an armed duel with no timer.
        """
```

The `deadline_id` for a state is `state.seq` — the seq after the event that set the anchor, which is what the runtime knows and what a later `ExpireTimer` will be compared against. Write that reasoning into the code, because it is the one thing a reader will otherwise have to reconstruct.

- [ ] **Step 4: Run, type-check, lint, commit**

```bash
git add backend/src/podvinsya/runtime/scheduler.py backend/tests/runtime/test_scheduler.py
git commit -m "Sleep until the deadline, and make a stale timer harmless"
```

---

### Task 6: The match runtime and its cycle

§6.2's loop, transcribed. Everything before this task exists to make these twenty lines both correct and dull.

**Files:**
- Create: `backend/src/podvinsya/runtime/match.py`
- Test: `backend/tests/runtime/test_match.py`

**Interfaces:**
- Consumes: `CommitPath` and Task 2's `CommandOutcome`, `DeadlineScheduler`, `QueuedCommand`, `SystemOrigin`, `Broadcaster`, `Clock`, `RuntimeCode`, `Quarantined`; `fold` from the domain.
- Produces: `MatchRuntime(match_id, state, commit_path, scheduler, broadcaster)` with `submit(command, origin)`, `run()`, `stop()`, and read-only `state` / `quarantined`.

**The cycle, from §6.2:**

```
qc = await queue.get()                          # nothing is open while we wait
async with uow.begin() as tx:
    ctx    = await materialiser.build(state, qc.command, tx)
    events = decide(state, qc.command, ctx)     # pure, microseconds
    if not events:
        outcome = NoOp()
    else:
        await tx.append(...)
        outcome = Committed(events)
# COMMIT — every lock is released here

if isinstance(outcome, NoOp):
    qc.origin.resolve_noop(); continue

state = fold(evolve, state, events)
reschedule_deadline()
publish()
qc.origin.resolve_ok()
```

Task 4 owns everything above the commit line. This task owns everything below it, and the order is the requirement: fold, then reschedule, then publish, then resolve. Publishing before rescheduling would send a frame whose deadline the runtime has not yet armed; resolving before publishing would let a caller act on an outcome the room has not seen.

**Three things the loop must get right.**

*Nothing externally visible happens under a lock.* The origin is resolved only after the transaction has closed — which Task 4 guarantees by returning rather than by calling back.

*A stale `ExpireTimer` is dropped before `decide` sees it.* §4.3 puts the identity check in the runtime, because `Duel` does not record which `seq` set its anchor and the domain therefore cannot check it. A mismatched identifier resolves as a no-op: it is a benign race, not a rejection.

*A broadcaster failure never quarantines.* «Убивать живую партию из-за одного сломанного сокета — превращать проблему клиента в аварию всей комнаты.» The commit is durable and memory is correct; the publish is wrapped, logged, and the loop moves on.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_committed_command_folds_reschedules_publishes_and_resolves() -> None:
    """In that order. Assert the order, not just that all four happened —
    a frame published before the deadline is armed advertises a timer the
    runtime is not yet keeping."""


async def test_the_published_frame_carries_the_seq_before_the_batch() -> None:
    """`base_seq` is what a subscriber uses to tell whether it missed
    something. Sending the post-fold seq would make every gap invisible."""


async def test_a_no_op_resolves_without_publishing() -> None:
    """Nothing was persisted, so there is nothing for the room to see."""


async def test_a_rejection_leaves_the_state_and_the_deadline_untouched() -> None:


async def test_a_stale_expire_timer_is_dropped_as_a_no_op() -> None:
    """A judgement re-anchors the duel and a timer already in flight for
    the old anchor arrives afterwards. Applying it would resolve a duel
    that has seconds left."""


async def test_a_current_expire_timer_is_applied() -> None:
    """The other half: the identity check must not swallow the real one."""


async def test_a_broadcaster_that_raises_does_not_stop_the_match() -> None:
    """§6.3. The next command must still be accepted and committed."""


async def test_a_broadcaster_that_raises_still_resolves_the_caller() -> None:
    """The caller's command succeeded. Learning otherwise because someone
    else's socket broke would be a lie about durable state."""


async def test_a_failed_outcome_quarantines_and_tells_the_caller() -> None:


async def test_a_content_shortfall_does_not_quarantine() -> None:
    """§6.3 draws exactly this line: «обычный отказ, не авария»."""


async def test_a_quarantined_match_refuses_everything_afterwards() -> None:
    """Including commands already sitting in the queue: each is resolved
    with QUARANTINED rather than left to hang."""


async def test_commands_are_consumed_one_at_a_time_in_order() -> None:
    """«На партию — одна последовательная очередь команд.» Submit several
    at once and assert they were decided against successive states."""


async def test_stopping_cancels_the_deadline_task() -> None:
    """A shutdown that leaves a timer armed leaves a task firing into a
    loop nobody is consuming."""
```

- [ ] **Step 2: Run, watch fail. Step 3: Write `runtime/match.py`**

```python
class MatchRuntime:
    """One live match: one queue, one consumer, one deadline."""

    async def _consume(self, queued: QueuedCommand) -> None:
        if self._quarantined:
            queued.origin.resolve_failed(RuntimeCode.QUARANTINED, "this match is quarantined")
            return
        if self._is_stale_timer(queued.command):
            # §4.3: the identifier is what makes cancellation races
            # harmless. This is a benign race, so it is a no-op and not a
            # rejection — a rejection would show the host an error for
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

    def _publish(self, base_seq: int, events: tuple[Event, ...]) -> None:
        """§6.3: a broadcaster failure is logged and the match plays on.

        The commit is durable and memory is correct. Killing a live match
        because one subscriber's socket broke turns a client's problem into
        the whole room's.
        """
        try:
            self._broadcaster.publish(self._match_id, base_seq, self._state, events)
        except Exception:
            logger.exception("broadcast failed for %s; the match continues", self._match_id)
```

`_quarantine` sets the flag, cancels the scheduler, and drains the queue resolving each waiting origin with `QUARANTINED` — a command left in a queue nobody consumes is a hung request.

- [ ] **Step 4: Run, type-check, lint, commit**

```bash
git commit -m "Run one match: one queue, one consumer, one deadline"
```

---

### Task 7: Recovery

**Files:**
- Create: `backend/src/podvinsya/runtime/recovery.py`
- Test: `backend/tests/runtime/test_recovery.py`

**Interfaces:**
- Consumes: `MatchRepositoryPort`, `MatchRuntime`, `Materialiser`'s `at` override, `SystemOrigin`.
- Produces: `async def recover(match_id, repository, …) -> MatchRuntime` — a runtime holding the folded state, paused if it was mid-duel.

**§4.4, in full:** «При загрузке партии, если дуэль в фазе RUNNING и якорь не пуст, рантайм ставит паузу и обнуляет списание за простой. Ведущий снимает с паузы, когда комната готова. Наивная свёртка списала бы весь простой инфраструктуры на того, кто в тот момент отвечал, — игрок проигрывал бы дуэль из-за перезапуска контейнера. Здесь простой становится видимым событием, а не тихо украденными секундами.»

The pause is issued as an ordinary `PauseDuel` through the ordinary loop, with the context built `at=duel.anchor` so `elapsed_ms` charges zero. The log then carries a real `DuelPaused` — the outage is a visible event, not silently stolen seconds.

- [ ] **Step 1: Write the failing tests** (integration-marked; these load a real log)

```python
async def test_a_match_mid_duel_comes_back_paused() -> None:


async def test_the_outage_is_charged_to_nobody() -> None:
    """The heart of §4.4. Persist a duel with an anchor, recover it a full
    minute of wall-time later, and assert both remainders are exactly what
    the log said before the crash. If this fails, a player loses a duel
    because a container restarted."""


async def test_the_pause_is_a_real_event_in_the_log() -> None:
    """«Простой становится видимым событием.» The seq after recovery is
    one greater than the log had, and the new row is a `duel.paused`."""


async def test_a_match_that_was_already_paused_is_not_paused_again() -> None:
    """`PauseDuel` on a paused duel is rejected by the domain. Recovery
    must not issue it — a `SystemOrigin` rejection means the runtime's own
    model was wrong, and it would be, here."""


async def test_a_match_with_no_duel_recovers_untouched() -> None:


async def test_a_match_in_setup_recovers_untouched() -> None:


async def test_a_finished_match_recovers_untouched() -> None:


async def test_recovery_folds_the_log_exactly() -> None:
    """Before any pause is issued, the state must equal what the domain
    produced in memory. Plan 2 proved that for `load`; this proves the
    runtime does not perturb it on the way in."""
```

- [ ] **Step 2-4: Run, watch fail, implement, commit**

```bash
git commit -m "Come back paused, charging the outage to nobody"
```

---

### Task 8: The watchdog

**Files:**
- Create: `backend/src/podvinsya/runtime/watchdog.py`
- Test: `backend/tests/runtime/test_watchdog.py`

**§4.3's exact condition:** «Сторожевой таймер ловит не отсутствие дедлайна, а условие „дуэль в фазе RUNNING, паузы нет, дедлайн не запланирован“.» A pause is a legitimate absent deadline; the fault is an armed duel with no timer, which is what a cancelled-but-not-recreated task leaves behind.

**Interfaces:** `Watchdog(clock, interval, runtimes)` with `sweep()` and a `run()` loop; `sweep` is public so a test drives one pass without a timer.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_running_duel_with_no_deadline_is_re_armed() -> None:


async def test_a_paused_duel_is_left_alone() -> None:
    """The whole reason the condition is three-part rather than „no
    deadline". A watchdog that re-armed a pause would take the host's only
    tool away from them mid-air."""


async def test_a_duel_with_a_deadline_is_left_alone() -> None:


async def test_a_match_with_no_duel_is_left_alone() -> None:


async def test_a_quarantined_match_is_left_alone() -> None:
    """It consumes nothing; arming a timer for it queues a command that
    will only be resolved with QUARANTINED."""


async def test_a_deadline_already_past_expires_rather_than_re_arming() -> None:
    """Re-arming a deadline that has already passed schedules a sleep that
    returns immediately and fires anyway — going straight to the expiry is
    the same outcome without the detour, and it is what §4.2 requires: the
    clock is authoritative."""


async def test_the_sweep_runs_on_the_clock_port() -> None:
```

- [ ] **Step 2-4: implement, commit**

```bash
git commit -m "Catch an armed duel with no timer"
```

---

### Task 9: The manager

**Files:**
- Create: `backend/src/podvinsya/runtime/manager.py`
- Test: `backend/tests/runtime/test_manager.py`

**Interfaces:** `MatchManager(repository, uow, materialiser_factory, broadcaster, clock)` with `async def start(match_id)`, `async def submit(match_id, command) -> CommandOutcome`, `async def shutdown()`, and `runtime_for(match_id)`.

**What it owes:** one `MatchRuntime` per live match and never two (`MatchAlreadyRunning`), recovery on first touch, a consumer task per match plus the watchdog as background tasks, and a shutdown that cancels every task and every deadline without leaving a caller hanging.

- [ ] **Step 1: Write the failing tests**

```python
async def test_starting_a_match_recovers_it_and_runs_a_consumer() -> None:


async def test_starting_the_same_match_twice_is_refused() -> None:
    """§6 gives each match one sequential queue. The optimistic append
    would catch a second writer, but catching it is a failure path."""


async def test_submitting_returns_the_outcome_the_loop_produced() -> None:


async def test_two_matches_run_independently() -> None:
    """A quarantine in one must not touch the other — they share a process
    and nothing else."""


async def test_shutdown_cancels_every_consumer_and_every_deadline() -> None:


async def test_shutdown_resolves_whoever_was_still_waiting() -> None:
    """A caller awaiting a future that nobody will ever complete is a hung
    request that survives the process it was made to."""
```

- [ ] **Step 2-4: implement, commit**

```bash
git commit -m "Hold one runtime per live match, and let go of them cleanly"
```

---

### Task 10: The failure story, end to end

The previous tasks tested each mechanism against fakes. This one drives the whole stack against a real PostgreSQL and asserts the properties §11 names.

**Files:**
- Test: `backend/tests/runtime/test_failure_policy.py`, `backend/tests/runtime/test_backpressure.py`

- [ ] **Step 1: Write the tests**

```python
async def test_a_match_survives_a_broadcaster_that_never_stops_failing() -> None:
    """§11's «ломающийся вещатель». Play several commands with a
    broadcaster that raises every time, then assert the log holds every one
    of them and the state is what the domain would have produced."""


async def test_a_recovered_match_replays_to_the_same_state() -> None:
    """§11's «Восстановление». Play a match, drop the runtime, recover it
    from the log alone, and compare — modulo the pause §4.4 adds."""


async def test_a_late_judgement_loses_to_the_clock() -> None:
    """§11's «Гонка дедлайна и судейства». Submit `JudgeCorrect` after the
    deadline has passed and assert the duel resolved as an expiry. §4.2:
    «Опоздавшее „Верно“ не воскрешает проигранную дуэль.»"""


async def test_pause_charges_only_the_unfrozen_intervals() -> None:
    """§11: «суммарное списанное время равно сумме незамороженных
    интервалов». Pause and resume several times across a duel and check
    the arithmetic against the clock the test itself drove."""


async def test_a_chain_of_undos_returns_the_state_bit_for_bit() -> None:
    """§11's «Отмена». Judge three times, undo three times, and compare the
    duel against the snapshot taken before the first judgement."""


async def test_publish_only_projects_and_never_awaits() -> None:
    """§6.1's contract, which the signature cannot express: a broadcaster
    that blocks must not be able to stall the loop. Drive a publisher whose
    queue is full and assert the loop's next command still completes within
    a bounded number of clock advances."""
```

- [ ] **Step 2: Implement whatever these reveal**

These are assertions about behaviour that should already exist. If one fails, the finding is in the task that owns that behaviour — fix it there, not with a special case here, and say in your report which task's code you changed and why.

- [ ] **Step 3: Full suite, mypy, ruff, commit**

```bash
git commit -m "Assert the failure policy against a real database"
```

---

## What this plan deliberately leaves undone

- **REST and WebSocket (§7)** — plan 4. `Broadcaster` and `Origin` are ports; the only production origin here is the one the runtime issues to itself.
- **The content library (§5.3, §8)** — plan 6. `CategoryBank` is declared here because the materialiser needs it today; its database-backed implementation is plan 6's.
- **Authentication (§7.5)** and **the two projections (§7.1)** — plan 4.
- **`MediaStore`** — named in §6.1's port list, needed by nothing in this plan. Plan 6 declares it when it has a consumer.
