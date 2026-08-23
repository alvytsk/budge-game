"""Where the test suite's database lives.

Owned here rather than by `Settings`: `Settings.database_url` has no default
precisely so an unset variable fails loudly. The test suite's own default
database is a test-suite concern, not something the production config type
should carry. It lives in an importable module rather than in a conftest so
a test file can import it without depending on how pytest happens to have
named the conftest's package.
"""

import asyncio
import os
from pathlib import Path

import budge.db
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TEST_DATABASE_URL = "postgresql+asyncpg://podvinsya:podvinsya@127.0.0.1:5434/podvinsya_test"

DATABASE_URL = os.environ.get("PODVINSYA_TEST_DATABASE_URL", TEST_DATABASE_URL)

# The object store beside it (§10), on the port `compose.test.yaml` maps.
# Owned here for the same reason `DATABASE_URL` is: the suite's own default
# is a suite concern, and `ApiSettings` deliberately has no default for any
# of these so an unset variable fails loudly in production.
TEST_S3_ENDPOINT = "http://127.0.0.1:9002"
S3_ENDPOINT = os.environ.get("PODVINSYA_TEST_S3_ENDPOINT", TEST_S3_ENDPOINT)
S3_ACCESS_KEY = "podvinsya"
S3_SECRET_KEY = "podvinsya-secret"
S3_BUCKET = "podvinsya-media-test"

ALEMBIC_INI = Path(budge.__file__).resolve().parent / "alembic.ini"


def alembic_config(url: str) -> Config:
    """`alembic.ini` deliberately carries no URL, so every caller — the CLI
    through `env.py`'s `Settings()` fallback, or a test — supplies one
    explicitly.

    `script_location` (and `prepend_sys_path`) are overridden the same way
    `budge.cli._config` does it: the ini's own values are relative, and
    Alembic resolves a relative `script_location` (and inserts a relative
    `prepend_sys_path` into `sys.path`) against the invocation directory,
    not against the ini file's location. Anchoring both to the installed
    package keeps this config (and the tests built on it) correct
    regardless of the pytest invocation's working directory.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    migrations_dir = Path(budge.db.__file__).parent / "migrations"
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("prepend_sys_path", str(migrations_dir.parent.parent.parent))
    return config


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
            await asyncio.sleep(0.01)
