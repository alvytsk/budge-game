"""§10's «учения по восстановлению».

What this proves, in order, and why each step is not the previous one:

1. `pg_restore` accepts the archive          — the file is whole (I4)
2. the restored schema is at a revision      — it is this application's schema
3. every match's log folds                   — the history is *usable* (I2)
4. every referenced digest was mirrored      — the pictures came too (I3)

Only the first of those is what a shell script would check, and it is the
weakest: an archive can restore perfectly into a database whose event log
this code can no longer replay.
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url

from podvinsya.backup.dump import libpq_url
from podvinsya.backup.paths import BackupRoot, Manifest, stamp
from podvinsya.backup.scratch import scratch_database
from podvinsya.db.engine import create_engine, sessionmaker_for
from podvinsya.db.repository import MatchRepository
from podvinsya.domain.ids import MatchId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrillReport:
    dump: str | None
    drilled_at: str
    scratch: str
    revision: str | None
    matches: int
    events: int
    missing_media: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.dump is not None and not self.failures and not self.missing_media


def _pg_restore(archive: str, into: str) -> None:
    finished = subprocess.run(
        ["pg_restore", "--no-owner", "--no-privileges", "--dbname", into, archive],
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        raise RuntimeError(f"pg_restore exited {finished.returncode}: {finished.stderr.strip()}")


async def _fold_every_match(scratch: str) -> tuple[int, int, tuple[str, ...]]:
    """I2. `MatchRepository.load` is the same path recovery uses: it reads
    the log, insists the first event is `MatchCreated`, and folds. Reusing
    it is what makes this a rehearsal rather than an imitation."""
    engine = create_engine(scratch)
    failures: list[str] = []
    matches = 0
    events = 0
    try:
        sessions = sessionmaker_for(engine)
        async with sessions() as session:
            ids = [row[0] for row in (await session.execute(text("SELECT id FROM matches"))).all()]
        repository = MatchRepository(sessions)
        for raw in ids:
            matches += 1
            try:
                log = await repository.read_events(MatchId(raw))
                events += len(log)
                await repository.load(MatchId(raw))
            except Exception as failure:  # every failure is a finding, not a crash
                failures.append(f"{raw}: {type(failure).__name__}: {failure}")
    finally:
        await engine.dispose()
    return matches, events, tuple(failures)


async def _revision_of(scratch: str) -> str | None:
    engine = create_engine(scratch)
    try:
        async with engine.connect() as connection:
            return (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    finally:
        await engine.dispose()


async def run(
    root: BackupRoot, *, database_url: str, expected_revision: str | None = None
) -> DrillReport:
    """Restore the newest backup into a scratch database and prove it."""
    root.prepare()
    drilled_at = stamp(datetime.now(timezone.utc))
    newest = root.newest()

    if newest is None:
        report = DrillReport(
            dump=None,
            drilled_at=drilled_at,
            scratch="",
            revision=None,
            matches=0,
            events=0,
            missing_media=(),
            failures=("no backup to drill",),
        )
        _write(root, report)
        return report

    manifest = Manifest.read(root.manifest_for(newest))
    # I1: the name is derived here and nowhere else, and it can never be
    # the configured database — it always carries the drill suffix.
    live = make_url(database_url).database or "budge"
    name = f"{live}_drill_{drilled_at}"

    failures: list[str] = []
    revision: str | None = None
    matches = events = 0

    try:
        async with scratch_database(database_url, name) as scratch:
            await asyncio.to_thread(_pg_restore, str(root.dump_for(newest)), libpq_url(scratch))
            revision = await _revision_of(scratch)
            if revision is None:
                failures.append("the restored database carries no alembic_version")
            elif expected_revision is not None and revision != expected_revision:
                failures.append(f"restored at revision {revision}, expected {expected_revision}")
            matches, events, fold_failures = await _fold_every_match(scratch)
            failures.extend(fold_failures)
    except Exception as failure:
        failures.append(f"{type(failure).__name__}: {failure}")

    # I3, checked against the manifest rather than against the restored
    # rows: the manifest is what the dump *said* it needed.
    missing = tuple(digest for digest in manifest.digests if not root.blob(digest).is_file())

    report = DrillReport(
        dump=newest,
        drilled_at=drilled_at,
        scratch=name,
        revision=revision,
        matches=matches,
        events=events,
        missing_media=missing,
        failures=tuple(failures),
    )
    _write(root, report)
    if not report.passed:
        logger.error("restore drill failed: %s", report)
    return report


def _write(root: BackupRoot, report: DrillReport) -> None:
    payload = asdict(report) | {"passed": report.passed}
    (root.drills / f"{report.drilled_at}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
