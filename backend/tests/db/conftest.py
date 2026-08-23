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

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from podvinsya.db.engine import create_engine, sessionmaker_for
from support.db import DATABASE_URL, alembic_config

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
            text(
                "TRUNCATE TABLE match_events, match_players, matches, images, categories "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def sessions(
    migrated_schema: None, engine: AsyncEngine
) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)
