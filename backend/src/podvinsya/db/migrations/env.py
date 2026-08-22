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
