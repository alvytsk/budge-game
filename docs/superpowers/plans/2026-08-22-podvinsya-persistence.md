# Персистентность «Подвинься» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the domain core durable — an append-only event log that is the single source of truth, a codec that survives refactoring, migrations, and a non-authoritative read model for the admin match list.

**Architecture:** PostgreSQL holds `match_events` as the authority; `matches` and `match_players` are a projection maintained in the same transaction as the append and rebuildable from the log at any time. Events cross the boundary through a codec built on per-class Pydantic `TypeAdapter`s, keyed by a frozen wire-name registry and versioned with `schema_version` plus an upcaster chain from day one. Appends are guarded optimistically by `expected_last_seq`; an ambiguous commit is settled by reconciling the batch itself against `(match_id, operation_id)`. Nothing here reads a clock, decides anything, or emits an event — the domain stays pure and the runtime (plan 3) stays absent.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async) + asyncpg, Alembic, Pydantic 2, PostgreSQL 16, pytest + pytest-asyncio + hypothesis.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md` — §5 is this plan's mandate; §3, §4 and §6 bound it.

## Global Constraints

- Python `>=3.12`. `mypy --strict` clean over `src/podvinsya` and `tests`; `ruff check` clean with `select = ["E4", "E7", "E9", "F", "E501"]` and `line-length = 100`.
- Dependency direction is one-way: `db` may import `domain`; `domain` imports nothing outside itself. **No file under `src/podvinsya/domain/` is modified by this plan.**
- «Лог — единственный источник истины; состояние в памяти — выбрасываемый кэш.» (§5.1) The log is append-only: no code in this plan updates or deletes a `match_events` row.
- «`operation_id` **всегда генерируется сервером**» (§5.1). This layer *accepts* one and never invents, defaults, or validates one — generation belongs to the runtime (plan 3).
- «`schema_version` и апкастеры с самого начала» (§5.1).
- «Проекционная таблица … Не авторитетна, перестраивается из лога.» (§5.2)
- «Миграции применяются отдельным шагом до старта приложения, а не при импорте.» (§10)
- Integration tests **fail rather than skip** when PostgreSQL is unreachable. A silently skipped integration suite reports green while proving nothing.
- No test waits on wall-clock time (§11). Every instant in a test is a value passed in.
- Code, identifiers and comments in English. User-facing Russian copy does not appear in this plan.

## Rulings made while writing this plan

Recorded here so no reviewer has to re-derive them.

1. **`UNIQUE (match_id, operation_id)` was wrong and the spec has been corrected** (commit `08b6956`). One command emits up to four events sharing one `operation_id` (`JudgePass` → `PassUsed` + `DuelResolved` + `PlayerEliminated` + `MatchWon`), so the constraint would reject the second row of its own batch. §6.3's reconciliation already assumes several rows share one `operation_id`. It is now a plain index; idempotency rests on the optimistic `expected_last_seq` guard plus reconciliation.
2. **Content tables (`categories`, `images`, `media`, §5.3) are not in this plan.** They gain no behaviour until §13's plan 6 (контент и админка), where the `categories.version` bump invariant and the `FOR SHARE` selection path can actually be tested. Creating the tables now would ship a schema nothing exercises.
3. **No `Protocol` ports yet.** §6.1 declares `UnitOfWork`, `MatchEventStore` and `MatchRepository` as ports, but a protocol with no consumer is speculative. The concrete classes here take exactly the signatures §6.2 shows verbatim, so plan 3 can extract the protocols without reshaping anything.
4. **Recovery pause (§4.4) is not implemented here.** `load` folds the log and returns what the log says. Pausing a duel that was running when the process died emits an event, and emitting events is the runtime's job.
5. **Payload array order for set-valued fields is unspecified.** `DuelResolved.absorbed_cells` is a `frozenset[Cell]`; Pydantic dumps it in set-iteration order. Verified empirically: substituting a sorted tuple to force determinism makes Pydantic emit a serializer warning. Nothing reads these arrays positionally — reconciliation compares `seq` and `type` only — so the round-trip property is the contract and the golden test canonicalises before comparing.
6. **`matches` carries only what §5.2 asks for**, plus `last_seq` for the optimistic guard. Board and settings live in `MatchCreated` and are read from there; copying them into a non-authoritative projection would invite someone to trust the copy.
7. **`match_players` has no `seat` column.** Turn order lives in `MatchStarted`. §5.2 wants "players" for an admin list, nothing more.

## File Structure

```
backend/pyproject.toml                              modify  deps, pytest config, mypy excludes
backend/compose.test.yaml                           create  PostgreSQL for the integration suite
backend/alembic.ini                                 create  no URL of its own — every caller supplies one
backend/src/podvinsya/config.py                     create  Settings; database_url has no default
backend/src/podvinsya/cli.py                        create  `podvinsya migrate`
backend/src/podvinsya/db/__init__.py                create
backend/src/podvinsya/db/base.py                    create  declarative Base
backend/src/podvinsya/db/engine.py                  create  engine + sessionmaker factories
backend/src/podvinsya/db/errors.py                  create  persistence-layer exceptions
backend/src/podvinsya/db/models.py                  create  Match, MatchPlayer, MatchEventRow
backend/src/podvinsya/db/codec/__init__.py          create  facade: encode, decode
backend/src/podvinsya/db/codec/registry.py          create  frozen wire names + current versions
backend/src/podvinsya/db/codec/codec.py             create  encode/decode + UTC normalisation
backend/src/podvinsya/db/codec/upcasters.py         create  the version chain (empty at v1)
backend/src/podvinsya/db/store.py                   create  UnitOfWork, TransactionContext, reconciliation
backend/src/podvinsya/db/projection.py              create  read-model writes and rebuild
backend/src/podvinsya/db/repository.py              create  MatchRepository: create, load
backend/src/podvinsya/db/migrations/env.py          create
backend/src/podvinsya/db/migrations/script.py.mako  create
backend/src/podvinsya/db/migrations/versions/0001_initial.py  create
backend/tests/support/__init__.py                   create
backend/tests/support/streams.py                    create  legal event streams for every later test
backend/tests/domain/conftest.py                    modify  import make_deal from tests.support
backend/tests/codec/test_registry.py                create
backend/tests/codec/test_codec.py                   create
backend/tests/codec/test_upcasters.py               create
backend/tests/codec/golden/rich_stream.json         create
backend/tests/db/conftest.py                        create
backend/tests/db/test_connection.py                 create
backend/tests/db/test_migrations.py                 create
backend/tests/db/test_schema.py                     create
backend/tests/db/test_store.py                      create
backend/tests/db/test_reconciliation.py             create
backend/tests/db/test_projection.py                 create
backend/tests/db/test_repository.py                 create
```

---

### Task 1: The database seam

Nothing here persists an event yet. This task earns the right to talk to PostgreSQL at all: dependencies, configuration, an engine, and a test suite that fails loudly when the database is missing.

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/compose.test.yaml`
- Create: `backend/src/podvinsya/config.py`
- Create: `backend/src/podvinsya/db/__init__.py`, `backend/src/podvinsya/db/base.py`, `backend/src/podvinsya/db/engine.py`
- Create: `backend/tests/support/__init__.py`, `backend/tests/support/db.py`
- Test: `backend/tests/db/conftest.py`, `backend/tests/db/test_connection.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Settings` (`podvinsya.config`), `create_engine(url, *, echo=False) -> AsyncEngine`, `sessionmaker_for(engine) -> async_sessionmaker[AsyncSession]`, `engine_for(url)` async context manager, `Base` (`podvinsya.db.base`), and the pytest fixtures `engine`, `sessions`, `clean_db` for every later task.

- [ ] **Step 1: Add dependencies and test configuration**

Replace the `dependencies`, `optional-dependencies` and `[tool.pytest.ini_options]` blocks of `backend/pyproject.toml`; leave `[tool.ruff]`, `[tool.hatch…]` and `[build-system]` untouched.

```toml
dependencies = [
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.10",
    "pydantic-settings>=2.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "hypothesis>=6.100",
    "ruff>=0.5",
    "mypy>=1.10",
]

[project.scripts]
podvinsya = "podvinsya.cli:main"

[tool.pytest.ini_options]
pythonpath = ["src", "tests"]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "integration: needs a live PostgreSQL (see compose.test.yaml)",
]
```

`pythonpath` gains `tests` so Task 3's `tests/support/streams.py` is importable as `support.streams` from every test directory. `asyncio_mode = "auto"` means an `async def` test needs no decorator.

Under `[tool.mypy]`, add the generated-migration exclusion — `op.create_table(...)` call sequences cannot be usefully typed, and only the generated bodies are excluded, never `env.py`:

```toml
exclude = ["src/podvinsya/db/migrations/versions/"]
```

Install: `cd backend && .venv/bin/python -m pip install -e ".[dev]"` (or `uv pip install -e ".[dev]"` if the venv was made with uv).

- [ ] **Step 2: Write `compose.test.yaml`**

```yaml
# PostgreSQL for the integration suite only. Port 5434 keeps it clear of a
# default local server (5432) and of the neighbouring triviador project (5433).
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: podvinsya
      POSTGRES_PASSWORD: podvinsya
      POSTGRES_DB: podvinsya_test
    ports:
      - "5434:5432"
    # The test database is disposable by definition: keeping it in a tmpfs
    # makes the suite faster and makes "it survived a reboot" impossible to
    # rely on by accident.
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U podvinsya -d podvinsya_test"]
      interval: 1s
      timeout: 3s
      retries: 30
```

Start it: `docker compose -f backend/compose.test.yaml up -d`.

- [ ] **Step 3: Write the failing connection test**

`backend/tests/db/test_connection.py`:

```python
"""The seam itself: can this suite reach PostgreSQL at all."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_the_engine_reaches_a_live_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        version = (await connection.execute(text("SELECT version()"))).scalar_one()
    assert isinstance(version, str)
    assert version.startswith("PostgreSQL")


async def test_the_session_factory_opens_a_usable_session(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
```

- [ ] **Step 4: Run it and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/db -v`
Expected: collection error — no `engine` fixture, no `podvinsya.db`.

- [ ] **Step 5: Write `config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    `database_url` deliberately has no default. An unset
    `PODVINSYA_DATABASE_URL` must fail loudly at startup rather than
    quietly pointing a production process at somebody's scratch database.
    """

    model_config = SettingsConfigDict(env_prefix="PODVINSYA_")

    database_url: str
```

- [ ] **Step 6: Write `db/base.py` and `db/engine.py`**

`base.py`:

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every table in this schema.

    Alembic's autogenerate compares `Base.metadata` against the live
    database, so every model must reach it by importing this class.
    """
```

`engine.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: callers read ORM objects after the transaction
    # context exits, and a lazy refresh at that point would be I/O against a
    # transaction that is already gone.
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def engine_for(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()
```

`db/__init__.py` stays empty.

- [ ] **Step 7: Write `tests/support/db.py`**

`tests/support/__init__.py` is empty. `tests/support/db.py`:

```python
"""Where the test suite's database lives.

Owned here rather than by `Settings`: `Settings.database_url` has no default
precisely so an unset variable fails loudly. The test suite's own default
database is a test-suite concern, not something the production config type
should carry. It lives in an importable module rather than in a conftest so
a test file can import it without depending on how pytest happens to have
named the conftest's package.
"""

import os

TEST_DATABASE_URL = "postgresql+asyncpg://podvinsya:podvinsya@127.0.0.1:5434/podvinsya_test"

DATABASE_URL = os.environ.get("PODVINSYA_TEST_DATABASE_URL", TEST_DATABASE_URL)
```

- [ ] **Step 8: Write `tests/db/conftest.py`**

```python
"""Fixtures for the integration suite.

Isolation is TRUNCATE between tests, not an outer transaction rolled back.
Several tests here need two connections to observe each other's committed
work — the optimistic append guard is precisely about cross-transaction
visibility — and a wrapping transaction would make those tests silently
meaningless.

`engine` is session-scoped and asyncpg binds its connections to the event
loop they were created on. So every async test in this directory, and every
async fixture built from `engine`, must run on that same loop: declared
per-fixture with `loop_scope="session"` and per-module with
`pytest.mark.asyncio(loop_scope="session")` in `pytestmark`. Forgetting the
module-level mark reproduces the exact "attached to a different loop" error
this arrangement exists to prevent, so `pytest_collection_modifyitems`
below fails collection for that, and for a missing `integration` mark —
without which `-m "not integration"` would deselect the tests but still
build the session-scoped engine, and the fast lane would quietly require
PostgreSQL.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from podvinsya.db.engine import create_engine, sessionmaker_for
from support.db import DATABASE_URL

THIS_DIR = Path(__file__).parent

UNREACHABLE = (
    f"Cannot reach the test database at {DATABASE_URL}.\n"
    "Start it with:  docker compose -f backend/compose.test.yaml up -d\n"
    "These tests fail rather than skip: a silently skipped integration suite "
    "reports green while proving nothing."
)


def _lacks_session_loop_scope(item: pytest.Item) -> bool:
    """True for an async test item that has not opted into the session loop.

    `asyncio_mode = "auto"` attaches an `asyncio` marker with empty kwargs to
    every async test; a module that adds `pytest.mark.asyncio(loop_scope=
    "session")` to its `pytestmark` overrides that. A sync test carries no
    `asyncio` marker at all and has no loop to scope, so it is exempt.
    """
    marker = item.get_closest_marker("asyncio")
    return marker is not None and marker.kwargs.get("loop_scope") != "session"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A conftest hook is registered for the whole session once loaded, not
    scoped to its own directory — `items` is everything collected anywhere.
    Both checks below therefore filter to this directory; without that, this
    hook would reject the entire fast lane."""
    db_items = [item for item in items if item.path.is_relative_to(THIS_DIR)]

    unmarked = sorted(
        {item.nodeid.split("::")[0] for item in db_items if "integration" not in item.keywords}
    )
    if unmarked:
        raise pytest.UsageError(
            "tests/db modules must declare `pytestmark = pytest.mark.integration`; "
            "missing in: " + ", ".join(unmarked)
        )

    missing_loop_scope = sorted(
        {item.nodeid.split("::")[0] for item in db_items if _lacks_session_loop_scope(item)}
    )
    if missing_loop_scope:
        raise pytest.UsageError(
            "tests/db async tests are built from the session-scoped `engine` and "
            'must carry `pytest.mark.asyncio(loop_scope="session")`. Missing in: '
            + ", ".join(missing_loop_scope)
        )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_engine(DATABASE_URL)
    try:
        async with eng.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # re-raised as a usable message via pytest.fail
        await eng.dispose()
        pytest.fail(f"{UNREACHABLE}\n\nunderlying error: {exc!r}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)
```

`migrated_schema` and `clean_db` arrive in Task 2, once there is a schema to migrate.

- [ ] **Step 9: Run the tests**

Run: `cd backend && .venv/bin/python -m pytest tests/db -v`
Expected: 2 passed. Then stop the container and run again — expected: failures whose message names `compose.test.yaml`, **not** skips. Restart the container afterwards.

Run the whole suite: `.venv/bin/python -m pytest` — expected: 225 passed (223 domain + 2 here).
Run `.venv/bin/python -m pytest -m "not integration"` — expected: 223 passed, 2 deselected, and **no** attempt to reach PostgreSQL (verify by stopping the container for this run).

- [ ] **Step 10: Check types and lint**

Run: `.venv/bin/mypy` then `.venv/bin/ruff check .` — both clean.

- [ ] **Step 11: Commit**

```bash
git add backend/pyproject.toml backend/compose.test.yaml backend/src/podvinsya/config.py \
        backend/src/podvinsya/db backend/tests/db backend/tests/support
git commit -m "Open a seam to PostgreSQL for the integration suite"
```

---

### Task 2: Schema, migrations, and the migrate command

**Files:**
- Create: `backend/src/podvinsya/db/models.py`
- Create: `backend/alembic.ini`, `backend/src/podvinsya/db/migrations/env.py`, `backend/src/podvinsya/db/migrations/script.py.mako`, `backend/src/podvinsya/db/migrations/versions/0001_initial.py`
- Create: `backend/src/podvinsya/cli.py`
- Modify: `backend/tests/db/conftest.py` (add `migrated_schema`, `clean_db`), `backend/tests/support/db.py` (add `alembic_config`)
- Test: `backend/tests/db/test_schema.py`, `backend/tests/db/test_migrations.py`

**Interfaces:**
- Consumes: `Base`, `create_engine`, `sessionmaker_for`, the `engine`/`sessions` fixtures.
- Produces: ORM classes `Match`, `MatchPlayer`, `MatchEventRow` (module `podvinsya.db.models`); fixtures `migrated_schema` and `clean_db`; `podvinsya migrate` on the command line.

- [ ] **Step 1: Write the failing schema tests**

`backend/tests/db/test_schema.py`:

```python
"""What the schema itself guarantees, asserted against a live PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Match, MatchEventRow, MatchPlayer
from podvinsya.domain.state import MatchStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _a_match(sessions: async_sessionmaker[AsyncSession], **overrides: object) -> Match:
    match = Match(id=uuid4(), status=MatchStatus.SETUP.value, last_seq=1)
    for key, value in overrides.items():
        setattr(match, key, value)
    async with sessions() as session, session.begin():
        session.add(match)
    return match


async def test_two_events_cannot_share_a_seq_within_one_match(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match = await _a_match(sessions)
    async with sessions() as session, session.begin():
        session.add(
            MatchEventRow(
                match_id=match.id, seq=1, operation_id="op-1",
                type="match.created", schema_version=1, payload={},
            )
        )
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                MatchEventRow(
                    match_id=match.id, seq=1, operation_id="op-2",
                    type="match.started", schema_version=1, payload={},
                )
            )


async def test_one_operation_id_may_cover_several_events(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A JudgePass emits up to four events under one operation_id. A unique
    constraint here would reject the batch's own second row."""
    match = await _a_match(sessions)
    async with sessions() as session, session.begin():
        for seq, wire in enumerate(
            ("duel.pass_used", "duel.resolved", "match.player_eliminated", "match.won"), start=1
        ):
            session.add(
                MatchEventRow(
                    match_id=match.id, seq=seq, operation_id="op-judge",
                    type=wire, schema_version=1, payload={},
                )
            )
    async with sessions() as session:
        rows = (
            await session.execute(
                select(MatchEventRow.seq).where(MatchEventRow.operation_id == "op-judge")
            )
        ).scalars().all()
    assert sorted(rows) == [1, 2, 3, 4]


async def test_an_event_cannot_name_a_match_that_does_not_exist(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                MatchEventRow(
                    match_id=uuid4(), seq=1, operation_id="op-1",
                    type="match.created", schema_version=1, payload={},
                )
            )


async def test_the_status_check_admits_exactly_the_domain_statuses(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Derived from MatchStatus, not from a list retyped here: a status added
    to the domain and forgotten in the migration fails this test."""
    for status in MatchStatus:
        await _a_match(sessions, id=uuid4(), status=status.value)
    with pytest.raises(DBAPIError):
        await _a_match(sessions, id=uuid4(), status="lobby")


async def test_a_player_row_belongs_to_exactly_one_match(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match = await _a_match(sessions)
    player_id = uuid4()
    async with sessions() as session, session.begin():
        session.add(
            MatchPlayer(
                match_id=match.id, player_id=player_id,
                name="Рей", colour="#e5484d", eliminated=False,
            )
        )
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                MatchPlayer(
                    match_id=match.id, player_id=player_id,
                    name="Рей", colour="#3b82f6", eliminated=False,
                )
            )


async def test_deleting_a_match_takes_its_log_and_players_with_it(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Only the admin ever deletes a match, and a log row orphaned from its
    match is unreadable — there is no genesis event to fold it onto."""
    match = await _a_match(sessions)
    async with sessions() as session, session.begin():
        session.add(
            MatchEventRow(
                match_id=match.id, seq=1, operation_id="op-1",
                type="match.created", schema_version=1, payload={},
            )
        )
        session.add(
            MatchPlayer(
                match_id=match.id, player_id=uuid4(),
                name="Рей", colour="#e5484d", eliminated=False,
            )
        )
    async with sessions() as session, session.begin():
        await session.execute(text("DELETE FROM matches WHERE id = :id"), {"id": match.id})
    async with sessions() as session:
        left = (await session.execute(select(MatchEventRow.seq))).scalars().all()
    assert left == []
```

- [ ] **Step 2: Write the failing migration tests**

`backend/tests/db/test_migrations.py`:

```python
"""The migration, not `create_all`, is what builds this schema — including in
tests. If the suite built the schema any other way, `alembic check` and the
tests would exercise two different schemas and a migration bug could only be
found in production."""

import asyncio

import pytest
from alembic import command
from alembic.util.exc import AutogenerateDiffsDetected
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from support.db import DATABASE_URL, alembic_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_the_migration_creates_every_table_the_models_declare(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert {"matches", "match_players", "match_events"} <= tables


async def test_models_and_migrations_do_not_disagree(migrated_schema: None) -> None:
    """`alembic check` autogenerates against the live schema and raises if
    anything differs. A column added to a model without a migration fails
    here rather than at the first INSERT in production."""
    try:
        await asyncio.to_thread(command.check, alembic_config(DATABASE_URL))
    except AutogenerateDiffsDetected as diffs:  # pragma: no cover - failure path
        pytest.fail(f"models and migrations disagree: {diffs}")


async def test_downgrade_removes_the_schema_and_upgrade_restores_it(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    config = alembic_config(DATABASE_URL)
    await asyncio.to_thread(command.downgrade, config, "base")
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert "match_events" not in tables
    await asyncio.to_thread(command.upgrade, config, "head")
    async with engine.connect() as connection:
        version = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
    assert version == "0001"
```

- [ ] **Step 3: Run both and watch them fail**

Run: `cd backend && .venv/bin/python -m pytest tests/db -v`
Expected: import errors — no `podvinsya.db.models`, no `alembic_config`.

- [ ] **Step 4: Write `db/models.py`**

```python
"""ORM models: the event log and the read model of §5.2.

`matches` and `match_players` are a projection, not an authority — they
carry only what the admin list needs, plus `last_seq` for the optimistic
append guard. Board and settings are deliberately absent: they live in the
`MatchCreated` event, and a copy here would invite someone to trust it.

The log table is `MatchEventRow`, not `MatchEvent`: the domain already has
an `Event` union of fifteen dataclasses, and a module importing both under
one name produces a type error far from its cause.

Nothing here declares update or delete machinery for `match_events`. The
log is append-only.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from podvinsya.db.base import Base
from podvinsya.domain.state import MatchStatus

_STATUSES = ", ".join(f"'{status.value}'" for status in MatchStatus)


class Match(Base):
    """One match. `status` mirrors the domain's `MatchStatus`, and the check
    constraint is built from that enum rather than a list retyped here, so a
    new status cannot silently diverge from what the database will accept.

    TEXT plus a check constraint rather than a PostgreSQL ENUM: adding a
    value to a PG enum has historically been restricted inside a
    transaction, and this set is small and stable enough that the constraint
    costs nothing.
    """

    __tablename__ = "matches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    winner_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # No default: creation always sets this to 1 explicitly, in the same
    # transaction that writes the genesis event at seq=1. A default of 0
    # would describe a row state that is never actually persisted.
    last_seq: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES})", name="ck_matches_status_valid"),
    )


class MatchPlayer(Base):
    """A player of one match. No seat column: turn order lives in
    `MatchStarted`, and §5.2 wants only "players" for the admin list."""

    __tablename__ = "match_players"

    match_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    colour: Mapped[str] = mapped_column(Text)
    eliminated: Mapped[bool] = mapped_column(Boolean, default=False)


class MatchEventRow(Base):
    """The append-only log. PK `(match_id, seq)`; never updated or deleted.

    `(match_id, operation_id)` is an index, not a unique constraint: one
    command emits up to four events sharing its `operation_id`, and §6.3's
    reconciliation counts those rows and compares their ordered types.
    """

    __tablename__ = "match_events"

    match_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_match_events_match_id_operation_id", "match_id", "operation_id"),
    )
```

- [ ] **Step 5: Write the Alembic scaffolding**

`backend/alembic.ini` — note it carries **no** `sqlalchemy.url`; every caller supplies one, which is what keeps a test from ever pointing at a real database by accident:

```ini
[alembic]
script_location = src/podvinsya/db/migrations
prepend_sys_path = src
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`src/podvinsya/db/migrations/env.py`:

```python
"""Alembic environment. Hand-written and inside mypy's strict scope — only
the generated migration bodies are excluded."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from podvinsya.config import Settings
from podvinsya.db.base import Base
from podvinsya.db.engine import create_engine

# Imported for the side effect of registering every table on Base.metadata,
# which is what autogenerate and `alembic check` compare against.
import podvinsya.db.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """A URL passed by the caller wins; otherwise read the environment.

    `alembic.ini` carries no URL of its own, so this is the only place a
    database is chosen, and `Settings` has no default to fall back on.
    """
    configured = config.get_main_option("sqlalchemy.url", None)
    return configured or Settings().database_url


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_engine(_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(_run_online())
```

`src/podvinsya/db/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create the empty directory `src/podvinsya/db/migrations/versions/`.

- [ ] **Step 6: Generate and review migration 0001**

```bash
cd backend
PODVINSYA_DATABASE_URL="postgresql+asyncpg://podvinsya:podvinsya@127.0.0.1:5434/podvinsya_test" \
  .venv/bin/alembic revision --autogenerate -m "initial" --rev-id 0001
```

Read the generated file. It must create the three tables, the FKs with `ondelete="CASCADE"`, the check constraint `ck_matches_status_valid`, and the index `ix_match_events_match_id_operation_id`, and `downgrade()` must drop all three. Fix anything autogenerate got wrong by hand — `alembic check` in Step 8 is what proves the result agrees with the models.

- [ ] **Step 7: Extend `tests/db/conftest.py`**

First add `alembic_config` to `tests/support/db.py`:

```python
from pathlib import Path

from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def alembic_config(url: str) -> Config:
    """`alembic.ini` deliberately carries no URL, so every caller — the CLI
    through `env.py`'s `Settings()` fallback, or a test — supplies one
    explicitly."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config
```

Then in `tests/db/conftest.py` add the imports `import asyncio`, `from alembic import command`, `from support.db import alembic_config`, and append:

```python
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_schema(engine: AsyncEngine) -> None:
    """Build the schema exactly once per session by running the migration —
    never `Base.metadata.create_all`. Using the migration is what keeps
    `alembic check` meaningful."""
    # Two statements, not one: asyncpg's prepared-statement protocol rejects
    # multiple commands in a single execute().
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    # `command.upgrade` ends in `env.py`'s `asyncio.run(...)`, which cannot be
    # called from inside a running loop — so it runs on its own thread.
    await asyncio.to_thread(command.upgrade, alembic_config(DATABASE_URL), "head")


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(migrated_schema: None, engine: AsyncEngine) -> AsyncIterator[None]:
    # Truncate BEFORE the test, not after: truncating on the way out leaves
    # the database dirty for any test that does not request this fixture, and
    # that dirt surfaces as a failure unrelated to whatever ran next.
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE TABLE match_events, match_players, matches RESTART IDENTITY CASCADE")
        )
    yield
```

Make `sessions` depend on `migrated_schema` so no test can reach a session before the schema exists:

```python
@pytest_asyncio.fixture(loop_scope="session")
async def sessions(
    migrated_schema: None, engine: AsyncEngine
) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)
```

- [ ] **Step 8: Write `cli.py`**

```python
"""The command line. `migrate` exists because §10 requires migrations to be
applied as a separate step before the application starts, never at import."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from podvinsya.config import Settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="podvinsya")
    subcommands = parser.add_subparsers(dest="command", required=True)
    migrate = subcommands.add_parser("migrate", help="apply migrations up to a revision")
    migrate.add_argument("--revision", default="head")

    args = parser.parse_args(argv)
    if args.command == "migrate":
        command.upgrade(_config(Settings().database_url), args.revision)
        return 0
    return 1  # pragma: no cover - argparse rejects anything else first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

Add to `tests/db/test_migrations.py`:

```python
async def test_the_migrate_command_brings_an_empty_database_to_head(
    migrated_schema: None, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from podvinsya.cli import main

    await asyncio.to_thread(command.downgrade, alembic_config(DATABASE_URL), "base")
    monkeypatch.setenv("PODVINSYA_DATABASE_URL", DATABASE_URL)
    assert await asyncio.to_thread(main, ["migrate"]) == 0
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    assert "match_events" in tables
```

- [ ] **Step 9: Run everything**

Run: `cd backend && .venv/bin/python -m pytest -v`
Expected: all green — 223 domain tests plus the new schema and migration tests.

Run: `.venv/bin/mypy` and `.venv/bin/ruff check .` — clean.

- [ ] **Step 10: Commit**

```bash
git add backend/alembic.ini backend/src/podvinsya backend/tests
git commit -m "Add the schema, its migration, and the migrate command"
```

---

### Task 3: Legal event streams for the tests that follow

Every later task needs real events: the codec needs something to round-trip, the store needs something to append, the projection needs a finished match, and recovery needs a match caught mid-duel. Hand-written event literals would drift from what `decide` actually emits, so this task builds them by driving the domain and recording what comes out.

**Files:**
- Create: `backend/tests/support/streams.py`
- Modify: `backend/tests/domain/conftest.py` (import `make_deal` instead of defining it)
- Test: `backend/tests/support/test_streams.py`

**Interfaces:**
- Consumes: the domain's `decide`, `evolve`/`fold`, `create_initial_state`, `legal_targets`.
- Produces: `make_deal(board, players, secrets) -> DealPlan`, `Recorded(state, events)`, `build_rich_stream() -> Recorded`, and the constants `BASE_TIME`, `COLOURS`.

- [ ] **Step 1: Write the failing coverage test**

`backend/tests/support/test_streams.py`:

```python
"""The stream builder is test infrastructure, so it gets its own tests.

Without the coverage assertion, an event type could quietly stop appearing
in the stream and every codec, store and projection test built on it would
keep passing while covering one type less.
"""

from collections import Counter
from typing import get_args

from podvinsya.domain.events import Event
from podvinsya.domain.state import MatchStatus
from support.streams import build_rich_stream


def test_the_rich_stream_contains_every_event_type() -> None:
    recorded = build_rich_stream()
    produced = {type(event) for event in recorded.events}
    assert produced == set(get_args(Event)), (
        "every later task's coverage depends on this stream exercising the "
        f"whole union; missing: {set(get_args(Event)) - produced}"
    )


def test_the_rich_stream_ends_in_a_won_match() -> None:
    recorded = build_rich_stream()
    assert recorded.state.status is MatchStatus.FINISHED
    assert recorded.state.winner is not None
    assert len(recorded.state.active_players()) == 1


def test_the_stream_is_a_foldable_log() -> None:
    """seq counts events, one per event, starting at one. Every later task
    relies on that identity to line the log's `seq` up with the state's.

    This covers `_Recorder.apply`'s bookkeeping and nothing more: it stays
    true under a duplicated or reordered command, so it is not a guard on
    the sequence the builder assembles. That is the next test's job.
    """
    recorded = build_rich_stream()
    assert recorded.state.seq == len(recorded.events)


def test_the_stream_has_the_expected_shape() -> None:
    """Pin the command sequence itself, hand-verified.

    Without this, a duplicated or misordered command in `build_rich_stream`
    changes what every later persistence task treats as ground truth and no
    test notices — verified: adding a second `JudgePass` leaves the seq/len
    identity true and the coverage assertion green.

    Judging, passing, pausing, resuming and undoing are each 1 because only
    the hand-played first duel exercises them; `AttackDeclared`,
    `DuelStarted` and `DuelResolved` are each 10, one per duel; the single
    elimination ends a two-player match.

    The expected values are deliberately literal. Changing the stream should
    be a decision someone makes, not something that drifts.
    """
    names = [type(event).__name__ for event in build_rich_stream().events]
    expected_counts = {
        "MatchCreated": 1,
        "PlayerAdded": 2,
        "SecretAssigned": 2,
        "BoardDealt": 1,
        "MatchStarted": 1,
        "AttackDeclared": 10,
        "DuelStarted": 10,
        "AnswerAccepted": 1,
        "PassUsed": 1,
        "DuelPaused": 1,
        "DuelResumed": 1,
        "JudgementUndone": 1,
        "DuelResolved": 10,
        "PlayerEliminated": 1,
        "MatchWon": 1,
    }
    assert Counter(names) == Counter(expected_counts)
    assert len(names) == sum(expected_counts.values()) == 44

    expected_first_duel = [
        "MatchCreated",
        "PlayerAdded",
        "SecretAssigned",
        "PlayerAdded",
        "SecretAssigned",
        "BoardDealt",
        "MatchStarted",
        "AttackDeclared",
        "DuelStarted",
        "AnswerAccepted",
        "PassUsed",
        "DuelPaused",
        "DuelResumed",
        "JudgementUndone",
        "DuelResolved",
    ]
    first_resolved = names.index("DuelResolved")
    assert names[: first_resolved + 1] == expected_first_duel


def test_the_stream_is_deterministic_in_shape() -> None:
    """Identifiers are fresh per call, but the sequence of event types is
    not allowed to wander — a flapping shape would make the golden payload
    file in Task 5 unmaintainable."""
    first = [type(event).__name__ for event in build_rich_stream().events]
    second = [type(event).__name__ for event in build_rich_stream().events]
    assert first == second
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/support -v`
Expected: `ModuleNotFoundError: No module named 'support.streams'`.

- [ ] **Step 3: Write `tests/support/streams.py`**

```python
"""Legal event streams, produced by driving the domain rather than by hand.

Everything downstream of the domain — the codec, the event store, the
projection, recovery — needs events that are exactly what `decide` emits.
Literals typed into a test drift from the real thing the moment a field is
added, and the drift is invisible: the test still passes, it just stops
describing the system.

`make_deal` lives here rather than in `tests/domain/conftest.py` because a
conftest is not importable from another test directory. The domain suite
imports it from here.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from podvinsya.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    CreateMatch,
    DealBoard,
    DeclareAttack,
    ExpireTimer,
    JudgeCorrect,
    JudgePass,
    PauseDuel,
    ResumeDuel,
    StartDuel,
    StartMatch,
    UndoLastJudgement,
)
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DealPlan, DealtCell, DecisionContext, JournalEntry
from podvinsya.domain.decide import decide
from podvinsya.domain.events import Event
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, MatchId, PlayerId
from podvinsya.domain.rules import legal_targets
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState, MatchStatus

BASE_TIME = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
COLOURS = ("#e5484d", "#3b82f6", "#22c55e", "#a855f7")

# Enough for one duel with room to undo and replay. The domain rejects with
# IMAGES_EXHAUSTED past the end of the pack, which no stream here reaches.
IMAGES_PER_DUEL = 24


def make_deal(
    board: BoardSize,
    players: tuple[PlayerId, ...],
    secrets: dict[PlayerId, CategoryId],
) -> DealPlan:
    """Deterministic deal on a Latin-square pattern, secret on each owner's first cell.

    Owner is (col + row) % n, so no two orthogonally adjacent cells share an
    owner and every neighbour is a legal target from move one. A plain
    round-robin over the row-major cell order would hand each player a solid
    column whenever the board width is a multiple of the player count, and
    then "the cell below is a legal target" stops being true.
    """
    n = len(players)
    cells = board.cells()
    per_player = len(cells) // n
    seen: set[PlayerId] = set()
    dealt: list[DealtCell] = []
    for cell in cells:
        owner = players[(cell.col + cell.row) % n]
        is_first_for_owner = owner not in seen
        seen.add(owner)
        category = secrets[owner] if is_first_for_owner else CategoryId(uuid4())
        dealt.append(
            DealtCell(
                cell=cell,
                owner=owner,
                category=category,
                group_id=GroupId(uuid4()),
                revealed=not is_first_for_owner,
            )
        )
    counts = Counter(d.owner for d in dealt)
    assert set(counts.values()) == {per_player}, f"uneven deal: {counts}"
    return DealPlan(cells=tuple(dealt))


@dataclass(frozen=True, slots=True)
class Recorded:
    """A folded state and the exact events that produced it."""

    state: MatchState
    events: tuple[Event, ...]


class _Recorder:
    """Drives commands through decide/evolve and keeps every event.

    It also assembles `duel_journal`, which is the runtime's job in
    production: the domain reads the journal but never builds it, and the
    rule that undo cannot cross into the previous duel lives entirely in
    whoever assembles it. Here that is the `journal.clear()` at the end of
    `_declare_and_start`, which runs at the start of every duel.
    """

    def __init__(self, match_id: MatchId, board: BoardSize, settings: MatchSettings) -> None:
        self.state = create_initial_state(match_id, board, settings)
        self.events: list[Event] = []
        self.journal: list[JournalEntry] = []

    def apply(self, command: Command, *, now: datetime, **ctx_kwargs: object) -> None:
        ctx = DecisionContext(now=now, **ctx_kwargs)  # type: ignore[arg-type]
        produced = decide(self.state, command, ctx)
        self.events.extend(produced)
        self.state = fold(self.state, produced)

    def snapshot(self) -> None:
        """Record the duel as it stands immediately before a judging event."""
        duel = self.state.duel
        assert duel is not None
        self.journal.append(
            JournalEntry(
                seq=self.state.seq,
                budgets=duel.budgets,
                answering=duel.answering,
                image_index=duel.index,
            )
        )


def _declare_and_start(recorder: _Recorder, now: datetime) -> None:
    """Attack with the current player's first group that has a legal target.

    One always exists while the match is running: the board is connected and
    the current player owns some but not all of it, so some cell of theirs
    borders a cell of someone else's.
    """
    attacker = recorder.state.current_player()
    attacking = next(
        group
        for group in recorder.state.groups_of(attacker)
        if legal_targets(recorder.state, group.id)
    )
    defending = sorted(legal_targets(recorder.state, attacking.id))[0]
    recorder.apply(
        DeclareAttack(attacking_group=attacking.id, defending_group=defending),
        now=now,
        image_order=tuple(ImageId(uuid4()) for _ in range(IMAGES_PER_DUEL)),
    )
    recorder.apply(StartDuel(), now=now)
    recorder.journal.clear()


def _expire(recorder: _Recorder, now: datetime) -> datetime:
    """Let the answering side's clock run out, which resolves the duel.

    The deadline is derived, never stored: anchor plus the answering
    player's remaining time (§4.1). A second past it is unambiguously late.
    """
    duel = recorder.state.duel
    assert duel is not None and duel.anchor is not None
    deadline = duel.anchor + timedelta(milliseconds=duel.budgets.get(duel.answering))
    now = deadline + timedelta(seconds=1)
    recorder.apply(ExpireTimer(deadline_id=0), now=now)
    return now


def build_rich_stream() -> Recorded:
    """One complete match containing every event type at least once.

    Two players on the smallest legal board: twelve cells, so at most
    eleven merges are available and the loop is bounded. This match ends
    sooner — in ten duels — because `MatchWon` fires when one player is
    left, not when one group is. The first duel is played by hand so that
    judging, passing, pausing, resuming and undoing all appear; the rest
    are decided by the clock, the shortest legal way to finish a match.
    """
    board = BoardSize(3, 4)
    settings = MatchSettings()
    recorder = _Recorder(MatchId(uuid4()), board, settings)
    players = (PlayerId(uuid4()), PlayerId(uuid4()))
    now = BASE_TIME

    recorder.apply(CreateMatch(board=board, settings=settings, player_count=2), now=now)
    for index, player_id in enumerate(players):
        recorder.apply(
            AddPlayer(player_id=player_id, name=f"Игрок {index + 1}", colour=COLOURS[index]),
            now=now,
        )
        recorder.apply(AssignSecret(player_id=player_id, category=CategoryId(uuid4())), now=now)
    recorder.apply(
        DealBoard(), now=now, deal=make_deal(board, players, dict(recorder.state.secrets))
    )
    recorder.apply(StartMatch(), now=now)

    _declare_and_start(recorder, now)
    now += timedelta(seconds=4)
    recorder.snapshot()
    recorder.apply(JudgeCorrect(), now=now)
    now += timedelta(seconds=3)
    recorder.snapshot()
    recorder.apply(JudgePass(), now=now)
    now += timedelta(seconds=2)
    recorder.apply(PauseDuel(), now=now)
    now += timedelta(seconds=30)  # the room waits; a pause charges nobody
    recorder.apply(ResumeDuel(), now=now)
    now += timedelta(seconds=1)
    recorder.apply(UndoLastJudgement(), now=now, duel_journal=tuple(recorder.journal))
    recorder.journal.pop()
    now = _expire(recorder, now)

    while recorder.state.status is not MatchStatus.FINISHED:
        _declare_and_start(recorder, now)
        now = _expire(recorder, now)

    return Recorded(state=recorder.state, events=tuple(recorder.events))
```

- [ ] **Step 4: Point the domain conftest at the shared `make_deal`**

In `backend/tests/domain/conftest.py`, delete the `make_deal` definition and its docstring, and add `from support.streams import make_deal` to the imports. Ruff will name any import that became unused (`Counter`, `DealPlan`, `DealtCell` and possibly others) — remove exactly those.

- [ ] **Step 5: Run everything**

Run: `cd backend && .venv/bin/python -m pytest -v`
Expected: the 223 domain tests still pass — that is what proves the moved `make_deal` is the same function — plus the four new stream tests.

- [ ] **Step 6: Check types and lint**

Run: `.venv/bin/mypy` and `.venv/bin/ruff check .` — clean.

- [ ] **Step 7: Commit**

```bash
git add backend/tests
git commit -m "Record legal event streams for the persistence tests"
```

---

### Task 4: The wire-name registry

**Files:**
- Create: `backend/src/podvinsya/db/codec/__init__.py`, `backend/src/podvinsya/db/codec/registry.py`
- Test: `backend/tests/codec/test_registry.py`

**Interfaces:**
- Consumes: `podvinsya.domain.events`.
- Produces: `WIRE_NAMES: Mapping[type[Any], str]`, `CLASSES_BY_WIRE_NAME: Mapping[str, type[Any]]`, `CURRENT_VERSION: Mapping[str, int]`.

- [ ] **Step 1: Write the failing test**

`backend/tests/codec/test_registry.py`:

```python
"""The registry is a frozen literal, so its tests are about completeness and
injectivity — the two properties a hand-maintained table loses first."""

from typing import get_args

from podvinsya.db.codec.registry import CLASSES_BY_WIRE_NAME, CURRENT_VERSION, WIRE_NAMES
from podvinsya.domain.events import Event


def test_every_event_in_the_union_has_a_wire_name() -> None:
    assert set(WIRE_NAMES) == set(get_args(Event))


def test_no_two_events_share_a_wire_name() -> None:
    """A collision would make one of the two undecodable, and the failure
    would surface as the wrong class coming back out of a log, not as an
    error."""
    assert len(set(WIRE_NAMES.values())) == len(WIRE_NAMES)


def test_wire_names_are_not_derived_from_class_names() -> None:
    """A renamed class must not be a data migration. If these were equal,
    someone would eventually generate them and the rename would silently
    orphan every row of the old name."""
    assert all(name != cls.__name__ for cls, name in WIRE_NAMES.items())


def test_the_reverse_index_covers_the_registry() -> None:
    assert CLASSES_BY_WIRE_NAME == {name: cls for cls, name in WIRE_NAMES.items()}


def test_every_wire_name_starts_at_version_one() -> None:
    assert CURRENT_VERSION == dict.fromkeys(WIRE_NAMES.values(), 1)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/codec -v`
Expected: `ModuleNotFoundError: No module named 'podvinsya.db.codec'`.

- [ ] **Step 3: Write `registry.py`**

```python
"""The frozen wire-name registry.

`WIRE_NAMES` maps each `Event` union member to the string stored in
`match_events.type`. It is a module-level literal — not derived from
`cls.__name__` — because the wire name and the Python class name are allowed
to diverge: a class can be renamed in a refactor without that being a data
migration, precisely because nothing here reads `__name__`. Changing a
*value* in this dict, on the other hand, is a data migration over
`match_events.type`.

`CURRENT_VERSION` gives every registered wire type a starting schema version
of 1. It is derived from `WIRE_NAMES`' keys — themselves a literal — so
every registered event is guaranteed an entry and none can be forgotten.
"""

from collections.abc import Mapping
from typing import Any

from podvinsya.domain.events import (
    AnswerAccepted,
    AttackDeclared,
    BoardDealt,
    DuelPaused,
    DuelResolved,
    DuelResumed,
    DuelStarted,
    JudgementUndone,
    MatchCreated,
    MatchStarted,
    MatchWon,
    PassUsed,
    PlayerAdded,
    PlayerEliminated,
    SecretAssigned,
)

WIRE_NAMES: Mapping[type[Any], str] = {
    MatchCreated: "match.created",
    PlayerAdded: "match.player_added",
    SecretAssigned: "match.secret_assigned",
    BoardDealt: "match.board_dealt",
    MatchStarted: "match.started",
    PlayerEliminated: "match.player_eliminated",
    MatchWon: "match.won",
    AttackDeclared: "duel.attack_declared",
    DuelStarted: "duel.started",
    AnswerAccepted: "duel.answer_accepted",
    PassUsed: "duel.pass_used",
    DuelPaused: "duel.paused",
    DuelResumed: "duel.resumed",
    JudgementUndone: "duel.judgement_undone",
    DuelResolved: "duel.resolved",
}

CLASSES_BY_WIRE_NAME: Mapping[str, type[Any]] = {name: cls for cls, name in WIRE_NAMES.items()}

CURRENT_VERSION: Mapping[str, int] = dict.fromkeys(WIRE_NAMES.values(), 1)
```

`codec/__init__.py` stays empty until Task 5 gives it something to re-export.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/codec -v` — expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/podvinsya/db/codec backend/tests/codec
git commit -m "Freeze the event wire names"
```

---

### Task 5: The codec's errors and the upcaster chain

**Files:**
- Create: `backend/src/podvinsya/db/errors.py`, `backend/src/podvinsya/db/codec/upcasters.py`
- Test: `backend/tests/codec/test_upcasters.py`

**Interfaces:**
- Consumes: `CURRENT_VERSION` (Task 4).
- Produces: the persistence layer's error module — `EventStreamCorrupt`, `UnknownEventType`, `UnknownSchemaVersion`, `NaiveDatetime` — plus the `Upcaster` type alias, `UPCASTERS: Mapping[tuple[str, int], Upcaster]`, `upcast_chain(wire_type, from_version) -> Upcaster`, and the injectable `_compose(upcasters, current_version, wire_type, from_version)`.

- [ ] **Step 1: Write the failing test**

`backend/tests/codec/test_upcasters.py`:

```python
"""The production tables are empty at v1, so every test here drives
`_compose` against a synthetic registry. Running the loop against the real
tables would prove nothing about the loop, the missing-step guard, or the
above-current guard — there would be nothing for it to do."""

from typing import Any

import pytest

from podvinsya.db.codec.upcasters import UPCASTERS, Upcaster, _compose, upcast_chain
from podvinsya.db.errors import UnknownSchemaVersion


def _rename(old: str, new: str) -> Upcaster:
    def step(payload: dict[str, Any]) -> dict[str, Any]:
        return {new if key == old else key: value for key, value in payload.items()}

    return step


SYNTHETIC: dict[tuple[str, int], Upcaster] = {
    ("duel.started", 1): _rename("at", "when"),
    ("duel.started", 2): _rename("when", "anchor"),
}
SYNTHETIC_CURRENT = {"duel.started": 3}


def test_the_chain_composes_every_step_in_order() -> None:
    upcast = _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 1)
    assert upcast({"at": "2026-08-22T12:00:00Z"}) == {"anchor": "2026-08-22T12:00:00Z"}


def test_a_payload_already_current_passes_through_untouched() -> None:
    upcast = _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 3)
    assert upcast({"anchor": "x"}) == {"anchor": "x"}


def test_a_partial_chain_starts_where_the_payload_is() -> None:
    upcast = _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 2)
    assert upcast({"when": "x"}) == {"anchor": "x"}


def test_a_missing_step_is_refused_rather_than_skipped() -> None:
    upcast = _compose({("duel.started", 2): _rename("when", "anchor")},
                      SYNTHETIC_CURRENT, "duel.started", 1)
    with pytest.raises(UnknownSchemaVersion):
        upcast({"at": "x"})


def test_a_version_newer_than_current_is_refused_immediately() -> None:
    """Refused when the chain is built, not when it runs: a log written by a
    newer deployment is a deployment problem, and reading it as if it were
    current would corrupt a match silently."""
    with pytest.raises(UnknownSchemaVersion):
        _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 4)


def test_the_production_registry_is_empty_at_version_one() -> None:
    assert UPCASTERS == {}


def test_the_production_chain_is_an_identity_today() -> None:
    assert upcast_chain("duel.started", 1)({"anchor": "x"}) == {"anchor": "x"}
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/codec/test_upcasters.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `db/errors.py`**

```python
"""Exceptions raised by the persistence layer.

One home for the whole layer's error surface: the codec's three live here
alongside the store's `ConcurrentModification` (Task 7) and the
repository's `MatchNotFound` (Task 10).
"""


class EventStreamCorrupt(Exception):
    """A log cannot be turned back into events.

    Every subclass is a *permanent* failure — retrying decodes the same
    bytes and fails the same way — which is what lets the runtime classify
    it as quarantine rather than retry.
    """


class UnknownEventType(EventStreamCorrupt):
    """`decode` was given a wire `type` absent from the registry."""


class UnknownSchemaVersion(EventStreamCorrupt):
    """`decode` was given a `schema_version` the upcaster chain cannot reach.

    Either it is newer than `CURRENT_VERSION` for that wire type, or an
    intermediate step is missing from the chain.
    """


class NaiveDatetime(EventStreamCorrupt):
    """A datetime reachable from an event has no `tzinfo`.

    There is no correct instant to recover, so the codec refuses rather than
    guessing a zone. Raised with a dotted path to the offending field.
    """
```

- [ ] **Step 4: Write `upcasters.py`**

```python
"""The upcaster chain: composes a payload forward from an old schema version
to the current one before Pydantic validates it.

`UPCASTERS` is empty at v1 — nothing has been renamed, retyped or removed
from any event yet, so there is nothing to compose. `_compose` is factored
out of `upcast_chain` so it can be exercised against a test-local synthetic
registry: the production tables give it nothing to do, and a test that ran
it against them would prove nothing about the loop or either guard.
"""

from collections.abc import Callable, Mapping
from typing import Any

from podvinsya.db.codec.registry import CURRENT_VERSION
from podvinsya.db.errors import UnknownSchemaVersion

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]

# (wire_type, from_version) -> transform producing from_version + 1.
UPCASTERS: Mapping[tuple[str, int], Upcaster] = {}


def _compose(
    upcasters: Mapping[tuple[str, int], Upcaster],
    current_version: Mapping[str, int],
    wire_type: str,
    from_version: int,
) -> Upcaster:
    """Build the transform for `wire_type` from `from_version` to current,
    without touching the module-level tables."""
    target = current_version[wire_type]
    if from_version > target:
        raise UnknownSchemaVersion(
            f"{wire_type} schema_version {from_version} is newer than current {target}"
        )

    def _chain(payload: dict[str, Any]) -> dict[str, Any]:
        version = from_version
        while version < target:
            step = upcasters.get((wire_type, version))
            if step is None:
                raise UnknownSchemaVersion(
                    f"no upcaster registered for {wire_type} v{version} -> v{version + 1}"
                )
            payload = step(payload)
            version += 1
        return payload

    return _chain


def upcast_chain(wire_type: str, from_version: int) -> Upcaster:
    """Compose forward until the payload matches the current version."""
    return _compose(UPCASTERS, CURRENT_VERSION, wire_type, from_version)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/codec -v` — expected: the registry and upcaster tests green. Task 6's codec tests do not exist yet.
Run: `.venv/bin/mypy` and `.venv/bin/ruff check .` — clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/podvinsya/db/errors.py backend/src/podvinsya/db/codec/upcasters.py \
        backend/tests/codec/test_upcasters.py
git commit -m "Let the log outlive a change to an event's shape"
```

---

### Task 6: Encoding and decoding events

**Files:**
- Create: `backend/src/podvinsya/db/codec/codec.py`
- Modify: `backend/src/podvinsya/db/codec/__init__.py`
- Test: `backend/tests/codec/test_codec.py`, `backend/tests/codec/golden/rich_stream.json`

**Interfaces:**
- Consumes: `WIRE_NAMES`, `CLASSES_BY_WIRE_NAME`, `CURRENT_VERSION` (Task 4); `upcast_chain` and the exceptions `EventStreamCorrupt`, `UnknownEventType`, `UnknownSchemaVersion`, `NaiveDatetime` (Task 5).
- Produces: `encode(event) -> tuple[str, int, dict[str, Any]]`, `decode(wire_type, schema_version, payload) -> Event`, `normalize_utc[T](value: T) -> T`.

**Verified facts** (probed against Pydantic 2.13 before this plan was written — do not re-derive):
- `TypeAdapter(SomeEventDataclass).dump_python(event, mode="json")` renders `UUID` NewTypes as strings, a `Cell` NamedTuple as `[col, row]`, `frozenset[Cell]` as a list of those pairs in **set-iteration order**, nested frozen dataclasses (`Budgets`) as objects, and an aware `datetime` as `"2026-08-22T12:00:00Z"`. `validate_python` restores all of them exactly, `frozenset` and `Cell` included.
- Substituting a sorted tuple into the `frozenset` field to force deterministic order makes Pydantic emit `PydanticSerializationUnexpectedValue`. Do not do it — see ruling 5.
- A **naive** datetime round-trips silently as naive. Pydantic will not catch it. That is the whole reason `normalize_utc` exists.

- [ ] **Step 1: Write the failing tests**

`backend/tests/codec/test_codec.py`:

```python
"""What the codec owes: an exact round trip for every event the domain can
emit, and a loud refusal for anything it cannot represent."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args

import pytest

from podvinsya.db.codec import decode, encode, normalize_utc
from podvinsya.db.codec.registry import CURRENT_VERSION, WIRE_NAMES
from podvinsya.db.errors import NaiveDatetime, UnknownEventType, UnknownSchemaVersion
from podvinsya.domain.board import Cell
from podvinsya.domain.events import DuelResolved, DuelStarted, Event
from support.streams import build_rich_stream

GOLDEN = Path(__file__).parent / "golden" / "rich_stream.json"


def _is_list_of_lists(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(i, list) for i in value)


def _canonical(payload: dict[str, Any]) -> dict[str, Any]:
    """Sort any list whose items are themselves lists.

    The only field that produces one is `DuelResolved.absorbed_cells`, a
    frozenset whose dumped order is arbitrary. Nothing reads these arrays
    positionally, so sorting for comparison loses no information — see the
    note in codec.py.
    """
    return {
        key: sorted(value) if _is_list_of_lists(value) else value
        for key, value in payload.items()
    }


def test_every_event_of_a_real_match_survives_a_round_trip() -> None:
    events = build_rich_stream().events
    for event in events:
        wire_type, version, payload = encode(event)
        assert json.loads(json.dumps(payload)) == payload, "payload must be JSON-native"
        assert decode(wire_type, version, payload) == event


def test_the_round_trip_covers_the_whole_event_union() -> None:
    """Without this, the loop above could silently cover twelve of fifteen."""
    events = build_rich_stream().events
    assert {type(event) for event in events} == set(get_args(Event))


def test_encode_reports_the_registered_wire_type_and_current_version() -> None:
    event = next(e for e in build_rich_stream().events if isinstance(e, DuelStarted))
    wire_type, version, _ = encode(event)
    assert wire_type == WIRE_NAMES[DuelStarted]
    assert version == CURRENT_VERSION[wire_type]


def test_a_non_utc_offset_is_normalised_rather_than_refused() -> None:
    """An offset denotes a real instant, so rejecting it would turn a
    harmless producer difference into a match that cannot load."""
    moscow = timezone(timedelta(hours=3))
    event = DuelStarted(anchor=datetime(2026, 8, 22, 15, 0, tzinfo=moscow))
    _, _, payload = encode(event)
    assert payload["anchor"] == "2026-08-22T12:00:00Z"
    assert decode("duel.started", 1, payload).anchor == datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def test_a_naive_datetime_is_refused_with_its_path() -> None:
    """Pydantic round-trips a naive datetime silently. There is no correct
    instant to recover from one, so the codec refuses rather than guessing."""
    event = DuelStarted(anchor=datetime(2026, 8, 22, 12, 0))
    with pytest.raises(NaiveDatetime) as excinfo:
        encode(event)
    assert "anchor" in str(excinfo.value)


def test_a_naive_datetime_nested_in_a_payload_is_refused_on_decode() -> None:
    _, _, payload = encode(DuelStarted(anchor=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)))
    payload["anchor"] = "2026-08-22T12:00:00"
    with pytest.raises(NaiveDatetime):
        decode("duel.started", 1, payload)


def test_an_unregistered_wire_type_is_refused() -> None:
    with pytest.raises(UnknownEventType):
        decode("duel.answer_was_wrong", 1, {})


def test_a_future_schema_version_is_refused() -> None:
    """A log written by a newer deployment must not be silently misread."""
    _, _, payload = encode(DuelStarted(anchor=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)))
    with pytest.raises(UnknownSchemaVersion):
        decode("duel.started", 99, payload)


def test_the_payload_shape_matches_the_golden_file() -> None:
    """The round-trip tests prove the codec is self-consistent; only a
    checked-in payload proves the shape on disk has not changed under a
    library upgrade."""
    events = build_rich_stream().events
    produced = [
        {"type": t, "schema_version": v, "payload": _canonical(p)}
        for t, v, p in (encode(event) for event in events)
    ]
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [row["type"] for row in produced] == [row["type"] for row in golden]
    assert [set(row["payload"]) for row in produced] == [set(row["payload"]) for row in golden]


def test_the_golden_file_decodes() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for row in golden:
        decoded = decode(row["type"], row["schema_version"], row["payload"])
        assert type(decoded).__name__ in {cls.__name__ for cls in WIRE_NAMES}


def test_a_decoded_cell_is_a_cell_and_not_a_bare_tuple() -> None:
    """`Cell` is a NamedTuple, and both the walk and Pydantic have to keep it
    one. A plain tuple compares equal to a Cell, so nothing else in this file
    would notice the difference."""
    resolved = next(e for e in build_rich_stream().events if isinstance(e, DuelResolved))
    wire_type, version, payload = encode(resolved)
    decoded = decode(wire_type, version, payload)
    assert isinstance(decoded, DuelResolved)
    cell = next(iter(decoded.absorbed_cells))
    assert type(cell) is Cell
    assert (cell.col, cell.row) == (cell[0], cell[1])


def test_the_codec_refuses_a_container_it_does_not_understand() -> None:
    """A list or set reachable from an event would have its contents skipped
    by the datetime walk. No event field uses one today; if one ever does,
    this fails rather than losing a nested timestamp."""

    @dataclass(frozen=True)
    class WithAList:
        moments: list[datetime]

    with pytest.raises(TypeError):
        normalize_utc(WithAList([datetime(2026, 8, 22, 12, 0, tzinfo=UTC)]))
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/codec -v`
Expected: import errors for `podvinsya.db.codec.decode` and `podvinsya.db.errors`.

- [ ] **Step 3: Write `codec/codec.py`**

```python
"""Encode and decode between domain events and JSONB-ready payloads.

Serialization is a Pydantic `TypeAdapter` per event class, not hand-rolled
reflection: the fifteen dataclasses nest `Budgets`, `DealtCell`, a `Cell`
NamedTuple, a `frozenset`, UUID NewTypes, tuples and optional datetimes,
which is exactly where a hand-written walker accumulates quiet bugs.
Adapters are cached per class — building one per event during a replay
would be pure waste.

One invariant Pydantic will not enforce is handled here explicitly: every
datetime reachable from an event must be aware and UTC. Pydantic accepts a
naive datetime on both the Python side and the JSON side, so `normalize_utc`
enforces it structurally on `encode` and on `decode`.

Payload array order for set-valued fields is not part of the contract.
`DuelResolved.absorbed_cells` is a `frozenset`, and Pydantic dumps it in
set-iteration order; nothing reads these arrays positionally — reconciliation
compares `seq` and `type` only — and forcing an order by substituting a
sorted tuple makes Pydantic emit a serializer warning.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import TypeAdapter

from podvinsya.db.codec.registry import CLASSES_BY_WIRE_NAME, CURRENT_VERSION, WIRE_NAMES
from podvinsya.db.codec.upcasters import upcast_chain
from podvinsya.db.errors import NaiveDatetime, UnknownEventType
from podvinsya.domain.events import Event

# A manual dict rather than `functools.cache`: mypy strict rejects a
# `type[X] | type[Y] | ...` argument against `functools`'s `Hashable`-typed
# wrapper, which would force a `type: ignore` at every call site instead of
# one comment here.
_ADAPTERS: dict[type[Any], TypeAdapter[Any]] = {}


def _adapter_for(cls: type[Any]) -> TypeAdapter[Any]:
    adapter = _ADAPTERS.get(cls)
    if adapter is None:
        adapter = TypeAdapter(cls)
        _ADAPTERS[cls] = adapter
    return adapter


def _walk(value: Any, path: str) -> Any:
    """Every datetime reachable from `value` must be aware and UTC.

    The walk is structural — driven by `isinstance` on the actual value
    tree, not by a list of "fields known to carry a datetime" — so a new
    datetime field on any future event inherits the invariant without anyone
    remembering to add it here.

    A naive value has no correct instant to recover, so it is rejected. An
    aware-but-not-UTC value denotes the correct instant, so it is normalized
    rather than rejected.

    `frozenset` is walked and rebuilt as a frozenset: `DuelResolved` carries
    one, and returning any other container from here would make Pydantic
    serialize a value that does not match the field's declared type. `list`
    and `set` are deliberately not traversed silently — no event field uses
    one today, and falling through to the leaf case would quietly skip any
    datetime nested inside.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise NaiveDatetime(path)
        return value.astimezone(UTC)
    if is_dataclass(value) and not isinstance(value, type):
        updates = {
            f.name: _walk(getattr(value, f.name), f"{path}.{f.name}") for f in fields(value)
        }
        instance: Any = value
        return replace(instance, **updates)
    if isinstance(value, tuple):
        walked = tuple(_walk(item, f"{path}[{i}]") for i, item in enumerate(value))
        # A NamedTuple — `Cell` — must be rebuilt as itself. Returning a plain
        # tuple here would hand Pydantic a value that no longer matches the
        # field's declared type, and the loss would be silent.
        return type(value)(*walked) if hasattr(value, "_fields") else walked
    if isinstance(value, frozenset):
        return frozenset(_walk(item, f"{path}{{}}") for item in value)
    if isinstance(value, Mapping):
        return {key: _walk(item, f"{path}[{key!r}]") for key, item in value.items()}
    if isinstance(value, list | set):
        raise TypeError(
            f"{path}: codec cannot walk a {type(value).__name__} — no event field uses "
            "one today, and this walk must not silently skip whatever it might contain"
        )
    return value


def normalize_utc[T](value: T) -> T:
    """Typed facade over `_walk`, which is `Any`-in `Any`-out because it
    recurses through heterogeneous fields. `encode` and `decode` both want
    their input type back, not `Any`."""
    return cast(T, _walk(value, "$"))


def encode(event: Event) -> tuple[str, int, dict[str, Any]]:
    wire_type = WIRE_NAMES[type(event)]
    normalized = normalize_utc(event)
    payload: dict[str, Any] = _adapter_for(type(event)).dump_python(normalized, mode="json")
    return wire_type, CURRENT_VERSION[wire_type], payload


def decode(wire_type: str, schema_version: int, payload: Mapping[str, Any]) -> Event:
    cls = CLASSES_BY_WIRE_NAME.get(wire_type)
    if cls is None:
        raise UnknownEventType(wire_type)
    upcast = upcast_chain(wire_type, schema_version)  # raises UnknownSchemaVersion
    event: Event = _adapter_for(cls).validate_python(upcast(dict(payload)))
    return normalize_utc(event)
```

`codec/__init__.py`:

```python
from podvinsya.db.codec.codec import decode, encode, normalize_utc

__all__ = ["decode", "encode", "normalize_utc"]
```

- [ ] **Step 4: Generate the golden file**

```bash
cd backend
.venv/bin/python - <<'PY'
import json, pathlib, sys
sys.path[:0] = ["src", "tests"]
from podvinsya.db.codec import encode
from support.streams import build_rich_stream

rows = [
    {"type": t, "schema_version": v, "payload": p}
    for t, v, p in (encode(e) for e in build_rich_stream().events)
]
out = pathlib.Path("tests/codec/golden/rich_stream.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(len(rows), "rows")
PY
```

Read the first few rows before committing them. `match.created` must carry `board`, `settings` and `player_count`; `duel.resolved` must carry `absorbed_cells` as a list of `[col, row]` pairs.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/codec -v` — expected: all green.
Run: `.venv/bin/mypy` and `.venv/bin/ruff check .` — clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/podvinsya/db backend/tests/codec
git commit -m "Encode events to JSONB and back without losing a timezone"
```

---

### Task 7: Genesis and the optimistic append

**Files:**
- Create: `backend/src/podvinsya/db/store.py`, `backend/src/podvinsya/db/repository.py`
- Modify: `backend/src/podvinsya/db/errors.py` (add `ConcurrentModification`), `backend/tests/support/db.py` (add the lock barrier)
- Test: `backend/tests/db/test_store.py`

**Interfaces:**
- Consumes: `encode`, `Match`, `MatchEventRow`, `sessionmaker_for`.
- Produces:
  - `UnitOfWork(sessions)` with `begin()` → async context manager yielding `TransactionContext`
  - `TransactionContext.append(match_id, *, expected_last_seq, events, operation_id) -> None` — the signature §6.2 shows verbatim
  - `MatchRepository(sessions)` with `create(match_id, event, *, operation_id) -> None`
  - `ConcurrentModification(match_id, expected_last_seq)`

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_store.py`:

```python
"""The append is the only way an event reaches the log, and the optimistic
guard is the only thing standing between two writers and a corrupted
stream."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.errors import ConcurrentModification
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import MatchId
from support.db import wait_until_a_backend_is_blocked_on
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _genesis(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[MatchId, tuple[Event, ...]]:
    """A created match plus the rest of a real stream, ready to append."""
    recorded = build_rich_stream()
    match_id = recorded.state.id
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    return match_id, recorded.events[1:]


async def test_create_writes_the_match_row_and_the_genesis_event(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, _ = await _genesis(sessions)
    async with sessions() as session:
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
        rows = (
            await session.execute(select(MatchEventRow).order_by(MatchEventRow.seq))
        ).scalars().all()
    assert match.last_seq == 1
    assert [(row.seq, row.type) for row in rows] == [(1, "match.created")]


async def test_a_batch_lands_contiguously_under_one_operation_id(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _genesis(sessions)
    batch = rest[:4]
    async with UnitOfWork(sessions).begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=batch, operation_id="op-batch")
    async with sessions() as session:
        rows = (
            await session.execute(
                select(MatchEventRow)
                .where(MatchEventRow.operation_id == "op-batch")
                .order_by(MatchEventRow.seq)
            )
        ).scalars().all()
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
    assert [row.seq for row in rows] == [2, 3, 4, 5]
    assert match.last_seq == 5


async def test_the_whole_stream_appends_one_command_at_a_time(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _genesis(sessions)
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(rest):
        async with uow.begin() as tx:
            await tx.append(
                match_id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    async with sessions() as session:
        seqs = (
            await session.execute(select(MatchEventRow.seq).order_by(MatchEventRow.seq))
        ).scalars().all()
    assert seqs == list(range(1, len(rest) + 2))


async def test_a_stale_expected_last_seq_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Someone else already advanced this match past what this caller's
    decide() saw. Appending anyway would write events decided against state
    that is no longer current."""
    match_id, rest = await _genesis(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:1], operation_id="op-a")
    with pytest.raises(ConcurrentModification):
        async with uow.begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[1:2], operation_id="op-b")
    async with sessions() as session:
        rows = (await session.execute(select(MatchEventRow.seq))).scalars().all()
    assert sorted(rows) == [1, 2], "the refused batch must leave no trace"


async def test_two_concurrent_appenders_cannot_both_win(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The UPDATE takes the row lock before any INSERT, so the second writer
    blocks in the database rather than racing to the same seq."""
    match_id, rest = await _genesis(sessions)
    uow = UnitOfWork(sessions)
    started = asyncio.Event()

    async def first() -> None:
        async with uow.begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[:1], operation_id="op-1")
            started.set()
            await wait_until_a_backend_is_blocked_on(sessions, "matches")

    async def second() -> None:
        await started.wait()
        async with uow.begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[1:2], operation_id="op-2")

    results = await asyncio.gather(first(), second(), return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrentModification)


async def test_an_empty_batch_is_a_programming_error(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.2 resolves a no-op without opening the log at all. Reaching append
    with nothing to write means the caller lost track of that."""
    match_id, _ = await _genesis(sessions)
    with pytest.raises(ValueError):
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=(), operation_id="op-empty")


async def test_a_failure_after_append_rolls_the_whole_batch_back(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _genesis(sessions)
    with pytest.raises(RuntimeError):
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[:2], operation_id="op-x")
            raise RuntimeError("the materialiser blew up after the append")
    async with sessions() as session:
        rows = (await session.execute(select(MatchEventRow.seq))).scalars().all()
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
    assert rows == [1]
    assert match.last_seq == 1, "last_seq must roll back with the rows it counts"
```

- [ ] **Step 2: Add the lock barrier to `tests/support/db.py`**

```python
async def wait_until_a_backend_is_blocked_on(
    sessions: async_sessionmaker[AsyncSession], relation: str, *, timeout_s: float = 5.0
) -> None:
    """Poll `pg_locks` from a third connection until PostgreSQL itself
    reports a backend blocked on a lock while running a statement that names
    `relation`.

    An `asyncio.Event` set before issuing the conflicting statement is not
    enough: `begin()` crosses an await boundary of its own, and the first
    side's commit can land before the second side's statement is even
    dispatched. Asking the database directly is what makes the contention
    deterministic.

    This does not filter on `pg_locks.relation`: the contention here blocks
    on a `transactionid` wait, not a relation-level lock — the relation-level
    intent locks do not conflict and are granted to both sides immediately.
    So it joins `pg_locks` (NOT granted) to `pg_stat_activity` on `pid` and
    matches the blocked backend's in-flight query text instead.

    A premature return cannot produce a false green: without the barrier the
    two sides simply interleave less often, and both paths converge on the
    same observable outcome — the UPDATE matching zero rows. `timeout_s` is
    a bound against a hung test, not the synchronization mechanism.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    async with sessions() as session:
        while True:
            blocked = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM pg_locks l "
                        "JOIN pg_stat_activity a ON a.pid = l.pid "
                        "WHERE NOT l.granted AND a.query ILIKE '%' || :relation || '%'"
                    ),
                    {"relation": relation},
                )
            ).scalar_one()
            if blocked:
                return
            if loop.time() > deadline:
                raise AssertionError(
                    f"timed out waiting for a backend to block on a lock against {relation!r}"
                )
```

- [ ] **Step 3: Run and watch it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/db/test_store.py -v`
Expected: `ModuleNotFoundError: No module named 'podvinsya.db.store'`.

- [ ] **Step 4: Add `ConcurrentModification` to `db/errors.py`**

```python
class ConcurrentModification(Exception):
    """`append`'s optimistic UPDATE matched zero rows.

    Raised with `(match_id, expected_last_seq)`. Someone else advanced this
    match's `last_seq` past what this attempt's `decide()` saw, so the
    runtime quarantines rather than retrying: a retry would append events
    decided against state that is no longer current.
    """
```

- [ ] **Step 5: Write `db/store.py`**

```python
"""The event store: one transaction, one optimistic append.

`append`'s signature is the one §6.2 writes out verbatim, so the runtime's
cycle can be transcribed from the spec without adaptation.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.codec import encode
from podvinsya.db.errors import ConcurrentModification
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.domain.events import Event
from podvinsya.domain.ids import MatchId


class TransactionContext:
    """Everything the runtime may do inside one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        """Write one command's events, or none of them.

        The UPDATE goes first, before any INSERT: it takes the match row's
        lock, so a competing appender blocks there rather than racing toward
        the same `seq`. Matching zero rows means someone else already moved
        `last_seq`, which is a `ConcurrentModification`, not a retry.

        `operation_id` arrives from the caller and is never invented here —
        §5.1 makes generating it the server's job, in the runtime.
        """
        if not events:
            raise ValueError(
                "append needs at least one event: §6.2 resolves a no-op without "
                "opening the log at all"
            )
        result = await self.session.execute(
            update(Match)
            .where(Match.id == match_id, Match.last_seq == expected_last_seq)
            .values(last_seq=expected_last_seq + len(events))
        )
        if result.rowcount != 1:
            raise ConcurrentModification(match_id, expected_last_seq)
        for offset, event in enumerate(events, start=1):
            wire_type, schema_version, payload = encode(event)
            self.session.add(
                MatchEventRow(
                    match_id=match_id,
                    seq=expected_last_seq + offset,
                    operation_id=operation_id,
                    type=wire_type,
                    schema_version=schema_version,
                    payload=payload,
                )
            )
        await self.session.flush()


class UnitOfWork:
    """Opens transactions. Nothing outside a `begin()` block writes."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[TransactionContext]:
        async with self._sessions() as session, session.begin():
            yield TransactionContext(session)
```

- [ ] **Step 6: Write `db/repository.py` with `create`**

```python
"""Reading and creating whole matches.

`create` exists as its own operation because genesis is the one append with
nothing to be optimistic about: `append` guards on a `matches` row that does
not exist yet. The match row and the seq-1 event are written in one
transaction, and every later event goes through `TransactionContext.append`.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.codec import encode
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchStatus


class MatchRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self, match_id: MatchId, event: MatchCreated, *, operation_id: str
    ) -> None:
        """Write the genesis event and the row the read model hangs off.

        The caller has already run `decide(CreateMatch)`; this layer never
        decides anything.
        """
        wire_type, schema_version, payload = encode(event)
        async with self._sessions() as session, session.begin():
            session.add(Match(id=match_id, status=MatchStatus.SETUP.value, last_seq=1))
            session.add(
                MatchEventRow(
                    match_id=match_id,
                    seq=1,
                    operation_id=operation_id,
                    type=wire_type,
                    schema_version=schema_version,
                    payload=payload,
                )
            )
```

Session type import note: `AsyncSession` is imported for the annotation on `async_sessionmaker[AsyncSession]`; ruff will flag it if it ends up unused.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/db -v` — expected: all green.
Run: `.venv/bin/mypy` and `.venv/bin/ruff check .` — clean.

- [ ] **Step 8: Commit**

```bash
git add backend/src/podvinsya/db backend/tests
git commit -m "Append a command's events, or none of them"
```

---

### Task 8: Reconciling an ambiguous commit

§6.3: «Реконсиляция сравнивает **саму пачку**, а не факт её существования … Совпало — коммит прошёл … Любое расхождение — карантин, никаких „почти совпало“.»

**Files:**
- Modify: `backend/src/podvinsya/db/store.py`
- Test: `backend/tests/db/test_reconciliation.py`

**Interfaces:**
- Produces: `Reconciliation` (StrEnum: `MATCHED`, `ABSENT`, `DIVERGED`) and `UnitOfWork.reconcile(match_id, operation_id, *, expected_last_seq, events) -> Reconciliation`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_reconciliation.py`:

```python
"""After a commit whose outcome is unknown, the runtime asks the log what
actually happened. Three answers, not two: a batch that never landed is a
retry, a batch that landed is progress, and anything else is a quarantine."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import Reconciliation, UnitOfWork
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import MatchId
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _seeded(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[MatchId, tuple[Event, ...]]:
    recorded = build_rich_stream()
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    return recorded.state.id, recorded.events[1:]


async def test_a_committed_batch_reconciles_as_matched(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    batch = rest[:3]
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=batch, operation_id="op-1")
    outcome = await uow.reconcile(match_id, "op-1", expected_last_seq=1, events=batch)
    assert outcome is Reconciliation.MATCHED


async def test_a_batch_that_never_landed_reconciles_as_absent(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Absent means retry, not quarantine — the distinction a boolean loses."""
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    outcome = await uow.reconcile(match_id, "op-ghost", expected_last_seq=1, events=rest[:3])
    assert outcome is Reconciliation.ABSENT


async def test_a_different_number_of_rows_diverges(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:3], operation_id="op-1")
    outcome = await uow.reconcile(match_id, "op-1", expected_last_seq=1, events=rest[:2])
    assert outcome is Reconciliation.DIVERGED


async def test_different_types_in_the_same_positions_diverge(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """"Almost matched" is not an outcome: the committed batch and the batch
    in memory must be the same batch."""
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:3], operation_id="op-1")
    reordered = (rest[1], rest[0], rest[2])
    outcome = await uow.reconcile(match_id, "op-1", expected_last_seq=1, events=reordered)
    assert outcome is Reconciliation.DIVERGED


async def test_a_batch_at_the_wrong_seq_range_diverges(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:2], operation_id="op-1")
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=3, events=rest[2:4], operation_id="op-2")
    outcome = await uow.reconcile(match_id, "op-2", expected_last_seq=1, events=rest[2:4])
    assert outcome is Reconciliation.DIVERGED
```

If the two events at `rest[0]` and `rest[1]` happen to share a wire type, the reordering test proves nothing — pick two indices with different types and say so in the test.

- [ ] **Step 2: Run and watch it fail**

Expected: `ImportError: cannot import name 'Reconciliation'`.

- [ ] **Step 3: Extend `store.py`**

```python
class Reconciliation(StrEnum):
    """What the log says about a batch whose commit outcome is unknown."""

    MATCHED = "matched"
    """The batch is durable. Fold it into memory and carry on."""

    ABSENT = "absent"
    """Nothing carries this operation_id. The commit did not land; retry."""

    DIVERGED = "diverged"
    """Something else is there. Quarantine — there is no "almost matched"."""
```

On `UnitOfWork`:

```python
    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        """Compare the batch itself against what the log holds.

        Not "does a row with this operation_id exist" — the exact seq range,
        the row count, and the ordered wire types, all three (§6.3). A
        partially applied batch, a batch at the wrong position, or a batch
        of different events is a divergence, and divergence is a quarantine.
        """
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(MatchEventRow.seq, MatchEventRow.type)
                    .where(
                        MatchEventRow.match_id == match_id,
                        MatchEventRow.operation_id == operation_id,
                    )
                    .order_by(MatchEventRow.seq)
                )
            ).all()
        if not rows:
            return Reconciliation.ABSENT
        expected_seqs = list(range(expected_last_seq + 1, expected_last_seq + 1 + len(events)))
        if [row.seq for row in rows] != expected_seqs:
            return Reconciliation.DIVERGED
        if [row.type for row in rows] != [WIRE_NAMES[type(event)] for event in events]:
            return Reconciliation.DIVERGED
        return Reconciliation.MATCHED
```

Add the imports this needs: `from enum import StrEnum`, `from sqlalchemy import select, update`, `from podvinsya.db.codec.registry import WIRE_NAMES`.

- [ ] **Step 4: Run, type-check, lint, commit**

Run: `.venv/bin/python -m pytest tests/db -v`, `.venv/bin/mypy`, `.venv/bin/ruff check .`

```bash
git add backend/src/podvinsya/db/store.py backend/tests/db/test_reconciliation.py
git commit -m "Settle an ambiguous commit by comparing the batch itself"
```

---

### Task 9: The read model

§5.2: «Проекционная таблица для списка партий в админке: идентификатор, статус, игроки, время создания, победитель. Не авторитетна, перестраивается из лога.»

**Files:**
- Create: `backend/src/podvinsya/db/projection.py`
- Modify: `backend/src/podvinsya/db/store.py` (apply the projection inside `append`)
- Test: `backend/tests/db/test_projection.py`

**Interfaces:**
- Produces: `apply_events(session, match_id, events) -> None` and `rebuild(session, match_id, events) -> None`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_projection.py`:

```python
"""The read model is not authoritative, and the test that matters is that it
says the same thing whether it was maintained event by event or rebuilt from
the log in one pass. Anything the incremental path can do that the rebuild
cannot is a bug in the incremental path."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Match, MatchPlayer
from podvinsya.db.projection import rebuild
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchStatus
from support.streams import Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _play_whole_match(sessions: async_sessionmaker[AsyncSession]) -> Recorded:
    recorded = build_rich_stream()
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(recorded.events[1:]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    return recorded


async def _snapshot(
    sessions: async_sessionmaker[AsyncSession], match_id: MatchId
) -> tuple[str, UUID | None, list[tuple[UUID, str, str, bool]]]:
    async with sessions() as session:
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
        players = (
            await session.execute(
                select(MatchPlayer)
                .where(MatchPlayer.match_id == match_id)
                .order_by(MatchPlayer.player_id)
            )
        ).scalars().all()
    return (
        match.status,
        match.winner_id,
        [(p.player_id, p.name, p.colour, p.eliminated) for p in players],
    )


async def test_the_projection_follows_the_match_to_its_end(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = await _play_whole_match(sessions)
    status, winner_id, players = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.FINISHED.value
    assert winner_id == recorded.state.winner
    assert len(players) == 2
    assert sum(1 for _, _, _, eliminated in players if eliminated) == 1
    assert {name for _, name, _, _ in players} == {"Игрок 1", "Игрок 2"}


async def test_a_rebuild_reproduces_the_incremental_projection(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = await _play_whole_match(sessions)
    incremental = await _snapshot(sessions, recorded.state.id)
    async with sessions() as session, session.begin():
        await rebuild(session, recorded.state.id, recorded.events)
    assert await _snapshot(sessions, recorded.state.id) == incremental


async def test_a_rebuild_discards_whatever_was_there_before(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Not authoritative means exactly this: a corrupted read model is
    repaired by replaying the log, not by hand."""
    recorded = await _play_whole_match(sessions)
    expected = await _snapshot(sessions, recorded.state.id)
    async with sessions() as session, session.begin():
        match = (
            await session.execute(select(Match).where(Match.id == recorded.state.id))
        ).scalar_one()
        match.status = MatchStatus.SETUP.value
        match.winner_id = None
    async with sessions() as session, session.begin():
        await rebuild(session, recorded.state.id, recorded.events)
    assert await _snapshot(sessions, recorded.state.id) == expected


async def test_the_projection_is_written_in_the_appending_transaction(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A projection updated after the commit can disagree with the log
    across a crash. It goes in the same transaction or it is wrong."""
    recorded = build_rich_stream()
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    with pytest.raises(RuntimeError):
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=1,
                events=recorded.events[1:3],
                operation_id="op-1",
            )
            raise RuntimeError("crash between the append and the commit")
    async with sessions() as session:
        players = (await session.execute(select(MatchPlayer))).scalars().all()
    assert players == []
```

- [ ] **Step 2: Run and watch it fail**

Expected: `ModuleNotFoundError: No module named 'podvinsya.db.projection'`.

- [ ] **Step 3: Write `db/projection.py`**

```python
"""The read model of §5.2, maintained in the appending transaction.

It carries what an admin list needs and nothing else. It is not
authoritative: `rebuild` replays the log over it, and the test that matters
is that both paths agree.

The projection reads *events*, not folded state, because §6.2 folds only
after the transaction has committed — inside the transaction, events are all
there is. That constraint is what lets `apply_events` serve both the
incremental path and the rebuild.
"""

from collections.abc import Iterable

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from podvinsya.db.models import Match, MatchPlayer
from podvinsya.domain.events import (
    Event,
    MatchStarted,
    MatchWon,
    PlayerAdded,
    PlayerEliminated,
)
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchStatus


async def apply_events(
    session: AsyncSession, match_id: MatchId, events: Iterable[Event]
) -> None:
    """Fold the read model forward over one batch. Events it does not
    recognize change nothing — most of the log is duel detail the admin list
    has no opinion about."""
    for event in events:
        match event:
            case PlayerAdded():
                session.add(
                    MatchPlayer(
                        match_id=match_id,
                        player_id=event.player_id,
                        name=event.name,
                        colour=event.colour,
                        eliminated=False,
                    )
                )
            case MatchStarted():
                await session.execute(
                    update(Match)
                    .where(Match.id == match_id)
                    .values(status=MatchStatus.RUNNING.value)
                )
            case PlayerEliminated():
                await session.execute(
                    update(MatchPlayer)
                    .where(
                        MatchPlayer.match_id == match_id,
                        MatchPlayer.player_id == event.player_id,
                    )
                    .values(eliminated=True)
                )
            case MatchWon():
                await session.execute(
                    update(Match)
                    .where(Match.id == match_id)
                    .values(status=MatchStatus.FINISHED.value, winner_id=event.player_id)
                )
            case _:
                pass
    await session.flush()


async def rebuild(session: AsyncSession, match_id: MatchId, events: Iterable[Event]) -> None:
    """Throw the read model away and replay the log over it."""
    await session.execute(delete(MatchPlayer).where(MatchPlayer.match_id == match_id))
    await session.execute(
        update(Match)
        .where(Match.id == match_id)
        .values(status=MatchStatus.SETUP.value, winner_id=None)
    )
    await apply_events(session, match_id, events)
```

- [ ] **Step 4: Call it from `append`**

In `TransactionContext.append`, after the event rows are added and before the flush:

```python
        await apply_events(self.session, match_id, events)
```

Add the import `from podvinsya.db.projection import apply_events`, and a note in `append`'s docstring: the read model is written here, inside the same transaction, because a projection updated after the commit can disagree with the log across a crash.

- [ ] **Step 5: Run, type-check, lint, commit**

Run: `.venv/bin/python -m pytest tests/db -v`, `.venv/bin/mypy`, `.venv/bin/ruff check .`

```bash
git add backend/src/podvinsya/db backend/tests/db/test_projection.py
git commit -m "Maintain the admin read model in the appending transaction"
```

---

### Task 10: Loading a match back

**Files:**
- Modify: `backend/src/podvinsya/db/repository.py`, `backend/src/podvinsya/db/errors.py`
- Test: `backend/tests/db/test_repository.py`

**Interfaces:**
- Produces: `LoadedMatch(state, last_seq)`, `MatchRepository.load(match_id) -> LoadedMatch`, `MatchNotFound`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_repository.py`:

```python
"""Recovery is «just fold the log» or it is nothing. These tests hold the
loaded state against the state the domain produced in memory — not against a
hand-written expectation, which could agree with a bug in both."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.errors import EventStreamCorrupt, MatchNotFound
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import DuelPhase
from support.streams import Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _persist(
    sessions: async_sessionmaker[AsyncSession],
    upto: int | None = None,
    *,
    recorded: Recorded | None = None,
) -> tuple[MatchId, tuple[Event, ...]]:
    """Persist a real stream, optionally truncated to catch a match mid-duel.

    `build_rich_stream` mints fresh random ids on every call, so a caller that
    needs to compare the loaded state against the exact state that produced it
    must pass that same `Recorded` in rather than let this helper build its
    own: two independent calls are never equal.
    """
    recorded = recorded if recorded is not None else build_rich_stream()
    events = recorded.events if upto is None else recorded.events[:upto]
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1:]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    return recorded.state.id, events


async def test_a_finished_match_loads_back_exactly(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    match_id, _ = await _persist(sessions, recorded=recorded)
    loaded = await MatchRepository(sessions).load(match_id)
    assert loaded.state == recorded.state, (
        "the fold of the persisted log must equal the fold that produced it"
    )
    assert loaded.last_seq == len(recorded.events)


async def test_a_match_caught_mid_duel_loads_with_its_duel_intact(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§11: «свёртка лога после падения посреди дуэли даёт … нетронутые
    остатки». The pause of §4.4 is the runtime's to emit; the log says what
    the log says."""
    recorded = build_rich_stream()
    cut = next(
        index
        for index, event in enumerate(recorded.events)
        if type(event).__name__ == "DuelResumed"
    ) + 1
    match_id, events = await _persist(sessions, upto=cut, recorded=recorded)
    loaded = await MatchRepository(sessions).load(match_id)
    duel = loaded.state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.RUNNING
    assert duel.anchor is not None, "the log recorded a running clock; loading must not pause it"
    assert loaded.last_seq == len(events)


async def test_the_loaded_seq_agrees_with_the_stored_last_seq(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Two counters of the same thing: the state's own seq, and the column
    the optimistic append guards on. They must never drift."""
    match_id, events = await _persist(sessions)
    loaded = await MatchRepository(sessions).load(match_id)
    async with sessions() as session:
        stored = (
            await session.execute(
                text("SELECT last_seq FROM matches WHERE id = :id"), {"id": match_id}
            )
        ).scalar_one()
    assert loaded.state.seq == loaded.last_seq == stored


async def test_an_unknown_match_is_not_an_empty_match(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(MatchNotFound):
        await MatchRepository(sessions).load(MatchId(uuid4()))


async def test_a_log_that_does_not_begin_with_genesis_is_corrupt(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Without MatchCreated there is no board and no settings to fold onto,
    so there is nothing to recover — and guessing would be worse.

    Seq 1 is rewritten rather than deleted: deleting it leaves a hole, and
    the contiguity check would then raise first, so this test would pass
    with the genesis check removed.
    """
    match_id, _ = await _persist(sessions, upto=4)
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE match_events SET type = 'duel.started', "
                "payload = '{\"anchor\": \"2026-08-22T12:00:00Z\"}'::jsonb "
                "WHERE match_id = :id AND seq = 1"
            ),
            {"id": match_id},
        )
    with pytest.raises(EventStreamCorrupt) as excinfo:
        await MatchRepository(sessions).load(match_id)
    assert "MatchCreated" in str(excinfo.value)


async def test_a_gap_in_the_log_is_corrupt(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, _ = await _persist(sessions, upto=5)
    async with sessions() as session, session.begin():
        await session.execute(
            text("DELETE FROM match_events WHERE match_id = :id AND seq = 3"), {"id": match_id}
        )
    with pytest.raises(EventStreamCorrupt):
        await MatchRepository(sessions).load(match_id)
```

- [ ] **Step 2: Run and watch it fail**

Expected: `ImportError: cannot import name 'MatchNotFound'`.

- [ ] **Step 3: Add `MatchNotFound` to `db/errors.py`**

```python
class MatchNotFound(Exception):
    """`load` was given a match id with no log behind it.

    Distinct from an empty or corrupt stream: a match that was never created
    is a caller mistake, not a data problem.
    """
```

- [ ] **Step 4: Add `load` to `MatchRepository`**

At module level in `repository.py`, above the class:

```python
@dataclass(frozen=True, slots=True)
class LoadedMatch:
    """A folded match and the log position it was folded from."""

    state: MatchState
    last_seq: int
```

As a method on `MatchRepository`:

```python
    async def load(self, match_id: MatchId) -> LoadedMatch:
        """Rebuild a match by folding its log, and nothing else.

        The genesis event supplies the board and settings
        `create_initial_state` needs, so they are read from the log rather
        than from the `matches` row: that row is a projection, and a
        projection must never become the thing recovery trusts. Folding
        `MatchCreated` again immediately afterwards is harmless — `evolve`
        assigns the same values it just supplied.

        §4.4's pause-on-recovery is deliberately absent. It emits an event,
        and this layer emits nothing.
        """
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(MatchEventRow)
                    .where(MatchEventRow.match_id == match_id)
                    .order_by(MatchEventRow.seq)
                )
            ).scalars().all()
        if not rows:
            raise MatchNotFound(match_id)
        if [row.seq for row in rows] != list(range(1, len(rows) + 1)):
            raise EventStreamCorrupt(f"{match_id}: the log has a gap or starts past seq 1")
        events = [decode(row.type, row.schema_version, row.payload) for row in rows]
        genesis = events[0]
        if not isinstance(genesis, MatchCreated):
            raise EventStreamCorrupt(
                f"{match_id}: the log begins with {type(genesis).__name__}, not MatchCreated"
            )
        state = fold(
            create_initial_state(match_id, genesis.board, genesis.settings), events
        )
        return LoadedMatch(state=state, last_seq=state.seq)
```

Add the imports: `from dataclasses import dataclass`, `from sqlalchemy import select`, `from podvinsya.db.codec import decode`, `from podvinsya.db.errors import EventStreamCorrupt, MatchNotFound`, `from podvinsya.domain.evolve import fold`, `from podvinsya.domain.genesis import create_initial_state`, `from podvinsya.domain.state import MatchState`.

- [ ] **Step 5: Run everything**

Run: `cd backend && .venv/bin/python -m pytest -v` — expected: the whole suite green, domain included.
Run: `.venv/bin/mypy` and `.venv/bin/ruff check .` — clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/podvinsya/db backend/tests/db/test_repository.py
git commit -m "Recover a match by folding its log"
```

---

## What this plan deliberately leaves undone

Named here so the final review does not report them as gaps.

- **Ports as `Protocol`s** (§6.1) — plan 3, where they get consumers.
- **The recovery pause** (§4.4) — plan 3. It emits an event.
- **`operation_id` generation** (§5.1) — plan 3. This layer accepts one.
- **Retry, quarantine, and the failure policy table** (§6.3) — plan 3. Task 8 gives it the three-way answer it needs; deciding what to do with each answer is the runtime's.
- **Content tables and the `categories.version` lock invariant** (§5.3) — plan 6.
- **The projection of a match list for the admin API** (§7) — plan 4 reads these tables; this plan only maintains them.
