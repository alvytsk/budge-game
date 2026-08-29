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
