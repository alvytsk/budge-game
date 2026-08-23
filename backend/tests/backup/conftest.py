"""Fixtures for the backup suite.

A real PostgreSQL, because the thing under test is `pg_dump` and
`pg_restore`. Every module here must carry both
`pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]`
— asyncpg binds a connection to the loop it was made on, and the engine
below is session-scoped.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from podvinsya.db.engine import create_engine, sessionmaker_for
from support.db import DATABASE_URL, alembic_config

UNREACHABLE = (
    f"Cannot reach the test database at {DATABASE_URL}.\n"
    "Start it with:  docker compose -f backend/compose.test.yaml up -d\n"
    "These tests fail rather than skip: a silently skipped backup suite "
    "reports green while proving nothing — which is the exact failure the "
    "restore drill exists to prevent."
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_engine(DATABASE_URL)
    try:
        async with eng.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # re-raised as a usable message
        await eng.dispose()
        pytest.fail(f"{UNREACHABLE}\n\nunderlying error: {exc!r}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await asyncio.to_thread(command.upgrade, alembic_config(DATABASE_URL), "head")


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(migrated_schema: None, engine: AsyncEngine) -> AsyncIterator[None]:
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
