"""Where a backup lives.

Nothing here touches a database. `BackupRoot` is pure path arithmetic, and
it is tested on its own because one of its methods takes a value that
arrives from a database column — and a path built from untrusted text is
how a backup directory becomes a way to write anywhere on the disk.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podvinsya.backup.paths import BackupRoot, Manifest, stamp

DIGEST = "a" * 64


def test_it_stamps_in_utc_whatever_zone_it_is_handed() -> None:
    """Kills on: using the local zone. Two backups an hour apart would sort
    out of order across a DST boundary, and `newest()` would restore the
    wrong one."""
    moment = datetime(2026, 8, 23, 20, 0, 0, tzinfo=timezone.utc)
    assert stamp(moment) == "20260823T200000Z"


def test_a_stamp_sorts_chronologically_as_text() -> None:
    """`newest()` sorts strings, so the format has to make that correct."""
    earlier = stamp(datetime(2026, 8, 23, 9, 0, 0, tzinfo=timezone.utc))
    later = stamp(datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc))
    assert earlier < later


def test_it_shards_a_blob_by_its_first_byte(tmp_path: Path) -> None:
    """One directory with a hundred thousand entries is slow to list and
    slower to sync. Two hex characters is 256 buckets, which is enough."""
    root = BackupRoot(tmp_path)
    assert root.blob(DIGEST) == tmp_path / "media" / "aa" / DIGEST


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "a" * 63, "a" * 65, "A" * 64, "../" + "a" * 61, "", "a/b"],
)
def test_it_refuses_anything_that_is_not_a_digest(tmp_path: Path, hostile: str) -> None:
    """The value reaches here from `images.media_sha256`, and the column's
    check constraint is the only thing that has ever validated it.

    Kills on: joining the value straight onto the path — a row whose digest
    read `../../etc/passwd` would make a backup run write outside its own
    directory, and the drill read outside it.
    """
    with pytest.raises(ValueError):
        BackupRoot(tmp_path).blob(hostile)


def test_prepare_makes_every_directory_a_backup_writes_into(tmp_path: Path) -> None:
    root = BackupRoot(tmp_path)
    root.prepare()
    assert root.dumps.is_dir()
    assert root.media.is_dir()
    assert root.drills.is_dir()


def test_prepare_is_safe_to_run_against_an_existing_root(tmp_path: Path) -> None:
    """Every backup run calls it. Kills on: `mkdir` without `exist_ok`,
    which would make the second backup the last one."""
    root = BackupRoot(tmp_path)
    root.prepare()
    root.prepare()
    assert root.dumps.is_dir()


def test_newest_is_none_before_anything_has_been_taken(tmp_path: Path) -> None:
    """Kills on: raising. The drill runs on a schedule and will meet an
    empty root on the first day; that is not an error, it is Tuesday."""
    root = BackupRoot(tmp_path)
    root.prepare()
    assert root.newest() is None


def test_newest_ignores_a_dump_with_no_manifest(tmp_path: Path) -> None:
    """A dump written but not yet manifested is a backup interrupted
    mid-run. Kills on: returning it — the drill would restore an archive
    whose digest list it cannot check, and report a pass it did not earn."""
    root = BackupRoot(tmp_path)
    root.prepare()
    root.dump_for("20260823T090000Z").write_bytes(b"archive")
    Manifest(taken_at="20260823T080000Z", revision="0002", digests=(DIGEST,)).write(
        root.manifest_for("20260823T080000Z")
    )
    root.dump_for("20260823T080000Z").write_bytes(b"archive")
    assert root.newest() == "20260823T080000Z"


def test_a_manifest_round_trips(tmp_path: Path) -> None:
    written = Manifest(taken_at="20260823T080000Z", revision="0002", digests=(DIGEST, "b" * 64))
    written.write(tmp_path / "m.json")
    assert Manifest.read(tmp_path / "m.json") == written
