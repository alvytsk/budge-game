"""The drill. Every test here restores a real archive into a real database.

That is the point: a drill tested against a mock is the thing it exists to
prevent.
"""

from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from podvinsya.backup import drill
from podvinsya.backup.dump import take
from podvinsya.backup.paths import BackupRoot, Manifest
from podvinsya.backup.scratch import scratch_database
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

    On its own this holds less than it reads as: a mutation pass showed it
    passing while `drill.run` restored straight into the configured
    database. `pg_restore` without `--clean` deletes nothing, and the rows
    still sitting in the live tables were what made its COPY abort — so the
    row below survived by collision, not by design. The two tests that
    follow hold the property this one only gestures at; keep all three.

    Kills on: restoring into the configured database *with* `--clean`, which
    would wipe production on a timer.
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


async def test_a_drill_does_not_bring_back_rows_deleted_since_the_backup(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I1 from the other side, and the side that has teeth.

    Its neighbour above only proves nothing was *deleted*, which a restore
    into the live database does not do: `pg_restore` without `--clean` drops
    nothing, and its COPY of a table that still holds the backed-up rows
    aborts on the primary key. So a drill aimed at production can fail that
    test while writing to production.

    Emptying the table first removes the accident that was doing the
    protecting. Now the backed-up rows have somewhere to land, and only the
    drill's own choice of database keeps them out of it.

    Kills on: restoring into the configured database — a scheduled job would
    resurrect every row deleted since the last backup, on a timer.
    """
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, title, is_secret, is_active, version) "
                "VALUES (:id, 'Удалённая', false, true, 1)"
            ),
            {"id": uuid4()},
        )
        await session.commit()

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    async with sessions() as session:
        await session.execute(text("DELETE FROM categories"))
        await session.commit()

    report = await drill.run(root, database_url=DATABASE_URL)

    async with sessions() as session:
        resurrected = (
            await session.execute(text("SELECT count(*) FROM categories"))
        ).scalar_one()
    assert resurrected == 0, "the drill restored the backup into the live database"
    assert report.passed, report.failures


async def test_no_database_the_drill_opens_is_the_live_one(
    clean_db: None,
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I1 as it is actually worded: the drill reads the configured
    database's *name* and never opens it.

    Every URL the drill hands to an engine or to `pg_restore` is recorded and
    checked, because "production survived this particular run" is a weaker
    claim than "production was never addressed". The first depends on what
    the archive happened to collide with; the second does not.

    Kills on: any URL in `drill.run` that names the configured database —
    read or write, restore target or revision probe.
    """
    await _a_match(sessions)
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    live = make_url(DATABASE_URL).database
    assert live is not None
    opened: list[str] = []

    real_pg_restore = drill._pg_restore

    def recording_create_engine(url: str) -> AsyncEngine:
        opened.append(url)
        return create_engine(url)

    def recording_pg_restore(archive: str, into: str) -> None:
        opened.append(into)
        real_pg_restore(archive, into)

    monkeypatch.setattr(drill, "create_engine", recording_create_engine)
    monkeypatch.setattr(drill, "_pg_restore", recording_pg_restore)

    report = await drill.run(root, database_url=DATABASE_URL)

    assert opened, "the drill opened nothing at all — the recording missed it"
    for url in opened:
        assert make_url(url).database != live, f"the drill opened the live database: {url}"
    assert report.passed, report.failures


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


async def test_the_scratch_database_is_dropped_with_a_connection_still_open(
    clean_db: None,
) -> None:
    """A bare `DROP DATABASE` refuses while anything is still connected, and
    the drill leaves connections behind it: `pg_restore` is a subprocess and
    the fold's engine closes its pool asynchronously.

    The neighbouring drop tests never see this — by the time they look, every
    connection has happened to land closed. Holding one open on purpose is
    the only way to ask whether the drop is actually forceful.

    Kills on: a `DROP DATABASE` without `WITH (FORCE)`. The scratch database
    would outlive its drill, and the next drill's `CREATE DATABASE` would
    meet it — so the job that exists to prove the backups work becomes the
    job that fails on a timer.
    """
    name = f"podvinsya_drill_force_{uuid4().hex[:12]}"
    lingering = None
    held = None
    try:
        async with scratch_database(DATABASE_URL, name) as scratch:
            lingering = create_engine(scratch)
            held = await lingering.connect()
            await held.execute(text("SELECT 1"))
            # Deliberately left open: the drop has to survive it.

        engine = create_engine(DATABASE_URL)
        try:
            async with engine.connect() as probe:
                remaining = (
                    await probe.execute(
                        text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                        {"name": name},
                    )
                ).scalar_one()
        finally:
            await engine.dispose()
        assert remaining == 0, "a connection left open kept the scratch database alive"
    finally:
        if held is not None:
            with suppress(Exception):
                await held.close()
        if lingering is not None:
            with suppress(Exception):
                await lingering.dispose()
