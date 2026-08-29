"""Taking a backup, against a real database and a real store."""

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.backup.dump import take
from budge.backup.paths import BackupRoot, Manifest
from support.db import DATABASE_URL
from support.media import InMemoryMediaStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _stock(sessions: async_sessionmaker[AsyncSession], digest: str) -> None:
    """One category and one image, which is the smallest thing a manifest
    can be non-empty about."""
    category = uuid4()
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


async def test_it_writes_an_archive_pg_restore_recognises(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I4: custom format, not SQL text.

    Kills on: `--format=plain`. A text dump replays statement by statement,
    so a truncated one restores its first half and leaves a database that
    looks plausible; `pg_restore` refuses a custom archive it cannot read
    whole.
    """
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    archive = root.dump_for(manifest.taken_at)
    assert archive.is_file()
    listed = subprocess.run(
        ["pg_restore", "--list", str(archive)], capture_output=True, text=True
    )
    assert listed.returncode == 0, listed.stderr
    assert "match_events" in listed.stdout


async def test_the_manifest_names_every_digest_the_dump_references(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I3's cross-check is only exact because this list is recorded at the
    moment the dump is taken.

    Kills on: an empty or inferred digest list — the drill would report a
    pass on a backup whose pictures were never copied.
    """
    digest = "c" * 64
    await _stock(sessions, digest)
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert digest in manifest.digests


async def test_it_copies_the_bytes_behind_every_digest(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Kills on: writing the manifest and not the blobs, which is the
    asymmetric failure I3 exists to catch — and which would otherwise be
    discovered on the night of a restore."""
    digest = "d" * 64
    await _stock(sessions, digest)
    store = InMemoryMediaStore()
    store.objects[digest] = b"a picture"

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=store)
    assert root.blob(digest).read_bytes() == b"a picture"


async def test_it_does_not_refetch_a_blob_it_already_mirrored(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I6: a digest names its bytes, so a mirrored blob is final.

    Kills on: re-downloading the whole library every run, which turns an
    hourly backup into an hourly transfer of everything ever uploaded.
    """
    digest = "e" * 64
    await _stock(sessions, digest)
    store = InMemoryMediaStore()
    store.objects[digest] = b"a picture"

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=store)
    first = store.gets
    await take(root, database_url=DATABASE_URL, media=store)
    assert store.gets == first


async def test_a_blob_the_store_has_lost_does_not_stop_the_backup(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The row is there and the object is not — which is exactly the state
    a backup most needs to record.

    Kills on: raising. The run would abort, no archive would be written,
    and the one night the store had a hole would also be the night with no
    backup at all. It belongs in the manifest so the drill reports it.
    """
    digest = "f" * 64
    await _stock(sessions, digest)
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert digest in manifest.digests
    assert not root.blob(digest).exists()


async def test_it_records_the_schema_revision(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """So a drill can say "restored, and at the revision this code expects"
    rather than only "restored"."""
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert manifest.revision is not None
    assert Manifest.read(root.manifest_for(manifest.taken_at)) == manifest


async def test_an_interrupted_dump_is_not_offered_as_a_backup(
    clean_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest is written last, so a run interrupted mid-way leaves
    nothing `stamps()` will offer to the drill.

    `pg_dump --file` creates its output the moment it opens it — a run that
    dies partway through leaves a truncated `.dump` on disk, as a direct
    `pg_dump` of a pattern that matches nothing demonstrates. So the file's
    existence proves nothing, and the missing manifest beside it is the only
    thing that tells `stamps()` this run never finished.

    Kills on: writing the manifest before the archive — the drill would pick
    a backup whose archive was never finished and report a failure that looks
    like corruption rather than an interrupted run.
    """
    from budge.backup import dump

    def explode(_database_url: str, into: str) -> None:
        Path(into).write_bytes(b"PGDMP")  # what an interrupted pg_dump leaves behind
        raise dump.BackupFailed("interrupted")

    monkeypatch.setattr(dump, "_pg_dump", explode)
    root = BackupRoot(tmp_path)
    with pytest.raises(dump.BackupFailed):
        await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    assert list(root.dumps.glob("*.dump")), "the interrupted run should have left its file"
    assert root.stamps() == ()


async def test_the_archive_references_nothing_the_manifest_does_not_name(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The order in this module's docstring, held as a property.

    The digest list is read once, and everything after it must be consistent
    with that read. Mirroring first pushes the whole media transfer in
    between the read and `pg_dump` — so an image uploaded during a mirror
    run of a library-sized store lands *inside* the archive while staying
    outside the manifest. The drill cross-checks the manifest (I3), so it
    would report a clean pass on an archive that references a picture nobody
    ever copied.

    The store below uploads one during the mirror loop, which is the only
    moment the two orders differ.

    Kills on: mirroring the blobs before running `pg_dump`.
    """
    mirrored = "1" * 64
    uploaded_mid_run = "2" * 64
    await _stock(sessions, mirrored)

    class _StoreThatUploadsMidRun(InMemoryMediaStore):
        """Something adds an image while the mirror loop is running."""

        async def get(self, digest: str) -> bytes | None:
            if not self.objects.get(uploaded_mid_run):
                self.objects[uploaded_mid_run] = b"arrived late"
                await _stock(sessions, uploaded_mid_run)
            return await super().get(digest)

    store = _StoreThatUploadsMidRun()
    store.objects[mirrored] = b"a picture"

    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=store)

    assert manifest.digests == (mirrored,)
    dumped = subprocess.run(
        [
            "pg_restore",
            "--data-only",
            "--table=images",
            "--file=-",
            str(root.dump_for(manifest.taken_at)),
        ],
        capture_output=True,
        text=True,
    )
    assert dumped.returncode == 0, dumped.stderr
    assert mirrored in dumped.stdout
    assert uploaded_mid_run not in dumped.stdout, (
        "the archive references a digest the manifest never named"
    )
