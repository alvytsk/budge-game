"""The scratch database a drill restores into.

I1: created for the drill, dropped when it ends, and never the configured
database. `drill.py` gets its scratch URL from here and has no other way to
name a database — which is what keeps "restore over production" from being
one typo away.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url

from budge.db.engine import create_engine

# Where `CREATE DATABASE` is issued from. A connection cannot create the
# database it is connected to, so this runs against the cluster's default
# maintenance database.
MAINTENANCE = "postgres"


def scratch_url(database_url: str, name: str) -> str:
    return make_url(database_url).set(database=name).render_as_string(hide_password=False)


def _maintenance_url(database_url: str) -> str:
    return scratch_url(database_url, MAINTENANCE)


@asynccontextmanager
async def scratch_database(database_url: str, name: str) -> AsyncIterator[str]:
    """Create `name`, yield a URL for it, and drop it on the way out —
    however the body ended.

    `CREATE DATABASE` and `DROP DATABASE` cannot run inside a transaction,
    hence AUTOCOMMIT. The identifier is quoted rather than interpolated:
    it is built from a timestamp here, but a helper that is only safe for
    its current caller is a trap for the next one.
    """
    engine = create_engine(_maintenance_url(database_url)).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    quoted = '"' + name.replace('"', '""') + '"'
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
            await connection.execute(text(f"CREATE DATABASE {quoted}"))
        try:
            yield scratch_url(database_url, name)
        finally:
            # WITH (FORCE) rather than a bare DROP: a connection the
            # restore left open would otherwise keep the database alive,
            # and the next drill would find it and fail.
            async with engine.connect() as connection:
                await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
    finally:
        await engine.dispose()
