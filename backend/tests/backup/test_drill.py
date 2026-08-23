"""The drill. Every test here restores a real archive into a real database.

That is the point: a drill tested against a mock is the thing it exists to
prevent.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.backup import drill
from podvinsya.backup.dump import take
from podvinsya.backup.paths import BackupRoot, Manifest
from podvinsya.db.engine import create_engine
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.ids import MatchId
from support.db import DATABASE_URL
from support.media import InMemoryMediaStore
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _a_match(sessions: async_sessionmaker[AsyncSession]) -> MatchId:
    """A match with a real, multi-event log.

    `build_rich_stream()` is the suite's existing generator of a legal
    stream (`tests/support/streams.py`); the genesis goes in through
    `MatchRepository.create` and the rest through a `UnitOfWork`, which is
    exactly what `tests/db/test_store.py` does. A drill that folded a
    genesis-only log would prove almost nothing — I2 is about a *history*
    still evolving.
    """
    recorded = build_rich_stream()
    match_id = recorded.state.id
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    rest = recorded.events[1:]
    if rest:
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest, operation_id="op-rest")
    return match_id


async def test_it_reports_a_pass_on_a_backup_it_just_took(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    await _a_match(sessions)
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    report = await drill.run(root, database_url=DATABASE_URL)
    assert report.passed, report.failures
    assert report.matches == 1
    assert report.events >= 1


async def test_it_never_touches_the_live_database(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I1, and the one property in this plan that must be impossible rather
    than unlikely.

    Kills on: restoring into the configured database — a scheduled job
    would then wipe production on a timer, replacing whatever happened
    since the last backup with the backup.
    """
    match_id = await _a_match(sessions)
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    # A row that exists ONLY after the backup was taken. A drill that
    # restored over the live database would delete it.
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, title, is_secret, is_active, version) "
                "VALUES (:id, 'После бэкапа', false, true, 1)"
            ),
            {"id": uuid4()},
        )
        await session.commit()

    await drill.run(root, database_url=DATABASE_URL)

    async with sessions() as session:
        survived = (
            await session.execute(
                text("SELECT count(*) FROM categories WHERE title = 'После бэкапа'")
            )
        ).scalar_one()
        kept = (
            await session.execute(
                text("SELECT count(*) FROM matches WHERE id = :id"), {"id": match_id}
            )
        ).scalar_one()
    assert survived == 1, "the drill deleted a row written after the backup"
    assert kept == 1


async def test_it_drops_the_scratch_database_afterwards(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Kills on: leaving it behind. An hourly drill would fill the disk
    with restored copies, and the first symptom would be production
    running out of space."""
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    report = await drill.run(root, database_url=DATABASE_URL)

    engine = create_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            remaining = (
                await connection.execute(
                    text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                    {"name": report.scratch},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert remaining == 0


async def test_it_drops_the_scratch_database_even_when_the_drill_fails(
    clean_db: None, tmp_path: Path
) -> None:
    """The failing path is the one that leaks. Kills on: a drop that only
    runs on success."""
    root = BackupRoot(tmp_path)
    root.prepare()
    # An archive pg_restore cannot read: the scratch database is created,
    # then the restore fails.
    root.dump_for("20260823T080000Z").write_bytes(b"not an archive")
    Manifest(taken_at="20260823T080000Z", revision="0002", digests=()).write(
        root.manifest_for("20260823T080000Z")
    )

    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed

    engine = create_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            remaining = (
                await connection.execute(
                    text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                    {"name": report.scratch},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert remaining == 0


async def test_it_fails_when_a_referenced_picture_was_never_mirrored(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I3: the asymmetric failure — rows saved, blobs missed.

    Kills on: checking only the database. The restore would report a clean
    pass, and the game would come back with every picture missing.
    """
    category = uuid4()
    digest = "b" * 64
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, title, is_secret, is_active, version) "
                "VALUES (:id, 'Кино', false, true, 1)"
            ),
            {"id": category},
        )
        await session.execute(
            text(
                "INSERT INTO images (id, category_id, media_sha256, answer_text, "
                "position, is_active) VALUES (:id, :category, :digest, 'Титаник', 0, true)"
            ),
            {"id": uuid4(), "category": category, "digest": digest},
        )
        await session.commit()

    root = BackupRoot(tmp_path)
    # The store has lost the object, so `take` mirrors nothing for it.
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed
    assert digest in report.missing_media


async def test_it_fails_when_a_restored_log_no_longer_folds(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I2, and the reason this lives in the application rather than in a
    shell script.

    A log whose first event is not `MatchCreated` decodes fine and restores
    fine — and cannot be recovered from. Kills on: proving only that
    `pg_restore` exited 0, which is the weakest property available and the
    one a shell script would check.
    """
    await _a_match(sessions)
    async with sessions() as session:
        await session.execute(text("UPDATE match_events SET type = 'PlayerAdded' WHERE seq = 1"))
        await session.commit()

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed
    assert report.failures


async def test_it_reports_nothing_to_drill_on_an_empty_root(tmp_path: Path) -> None:
    """First run of a fresh deployment. Kills on: raising — the scheduler
    would log a crash every day until the first backup landed."""
    root = BackupRoot(tmp_path)
    root.prepare()
    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed
    assert report.dump is None


async def test_it_writes_its_report_where_an_operator_will_find_it(
    clean_db: None, tmp_path: Path
) -> None:
    """A drill nobody can read the result of is a drill nobody ran."""
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    report = await drill.run(root, database_url=DATABASE_URL)
    written = list(root.drills.glob("*.json"))
    assert len(written) == 1
    assert report.drilled_at in written[0].name
