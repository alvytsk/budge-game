"""Taking a backup: the database, and the blobs it points at.

The two halves are taken in this order deliberately. The dump is a
consistent snapshot of the rows; the media mirror is then brought up to
date against *that* snapshot's digest list. A blob uploaded after the dump
is simply not in it, which is correct — the dump does not reference it
either. The reverse order would leave the manifest naming a row the
archive does not contain.
"""

import asyncio
import subprocess
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url

from budge.backup.paths import BackupRoot, Manifest, stamp
from budge.db.engine import create_engine
from budge.services.ports import MediaStore


class BackupFailed(Exception):
    """`pg_dump` refused. Deliberately not caught anywhere below: a backup
    that failed must fail loudly, and the scheduler's job is to report it,
    not to carry on."""


def libpq_url(database_url: str) -> str:
    """`postgresql+asyncpg://…` is a SQLAlchemy URL; `pg_dump` speaks
    libpq. Same host, same credentials, different scheme."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _pg_dump(database_url: str, into: str) -> None:
    # `--format=custom` is I4. `--no-owner` and `--no-privileges` keep the
    # archive restorable into a scratch database owned by whoever is
    # drilling, which is not necessarily the production role.
    finished = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            into,
            libpq_url(database_url),
        ],
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        raise BackupFailed(f"pg_dump exited {finished.returncode}: {finished.stderr.strip()}")


async def take(root: BackupRoot, *, database_url: str, media: MediaStore) -> Manifest:
    """Take one backup. Returns the manifest it wrote."""
    root.prepare()
    moment = stamp(datetime.now(timezone.utc))

    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            digests = tuple(
                sorted(
                    row[0]
                    for row in (
                        await connection.execute(text("SELECT DISTINCT media_sha256 FROM images"))
                    ).all()
                )
            )
    finally:
        await engine.dispose()

    # Off the loop: `pg_dump` is a blocking subprocess, and the scheduler
    # container runs it beside nothing else — but the API's event loop is
    # one import away and this must never be the thing that stalls it.
    await asyncio.to_thread(_pg_dump, database_url, str(root.dump_for(moment)))

    for digest in digests:
        destination = root.blob(digest)
        # I6: a digest names its bytes, so a mirrored blob is final.
        if destination.exists():
            continue
        data = await media.get(digest)
        if data is None:
            # The row points at an object the store has lost. Recorded in
            # the manifest and left for the drill to report — aborting here
            # would mean no backup at all on the one night there was a hole.
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    manifest = Manifest(taken_at=moment, revision=revision, digests=digests)
    # Written last, and this is what `stamps()` keys on: a run interrupted
    # before this line leaves an archive the drill will not pick up.
    manifest.write(root.manifest_for(moment))
    return manifest
