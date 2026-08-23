"""Where a backup lives, and how it is named.

Layout:

    <root>/dumps/20260823T200000Z.dump    pg_dump --format=custom
    <root>/dumps/20260823T200000Z.json    the manifest for that dump
    <root>/media/aa/aaaa…                 the shared blob mirror (I6)
    <root>/drills/20260823T210000Z.json   what a drill found

The mirror is shared across dumps and never pruned: a digest names its own
bytes (§7.6) so a blob never changes, §5.3 deletes content only softly, and
an old dump has to stay restorable.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# Fixed-width, UTC, and lexicographically chronological — which is what
# lets `newest()` sort strings instead of parsing every name.
_STAMP = re.compile(r"^\d{8}T\d{6}Z$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(STAMP_FORMAT)


@dataclass(frozen=True)
class Manifest:
    """What one dump references.

    `digests` is what makes the drill's media cross-check exact (I3): it is
    the set of blobs the restored database will ask for, recorded at the
    moment the dump was taken rather than inferred afterwards.
    """

    taken_at: str
    revision: str | None
    digests: tuple[str, ...]

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "taken_at": self.taken_at,
                    "revision": self.revision,
                    "digests": list(self.digests),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            taken_at=loaded["taken_at"],
            revision=loaded["revision"],
            digests=tuple(loaded["digests"]),
        )


@dataclass(frozen=True)
class BackupRoot:
    path: Path

    @property
    def dumps(self) -> Path:
        return self.path / "dumps"

    @property
    def media(self) -> Path:
        return self.path / "media"

    @property
    def drills(self) -> Path:
        return self.path / "drills"

    def dump_for(self, moment: str) -> Path:
        return self.dumps / f"{moment}.dump"

    def manifest_for(self, moment: str) -> Path:
        return self.dumps / f"{moment}.json"

    def blob(self, digest: str) -> Path:
        """The mirror path for one blob.

        The digest arrives from `images.media_sha256`, and the column's
        check constraint is the only thing that has ever validated it. This
        re-checks rather than trusting: a path built by joining untrusted
        text is how a backup directory becomes a way to write anywhere.
        """
        if not _DIGEST.match(digest):
            raise ValueError(f"not a sha256 digest: {digest!r}")
        return self.media / digest[:2] / digest

    def prepare(self) -> None:
        for directory in (self.dumps, self.media, self.drills):
            directory.mkdir(parents=True, exist_ok=True)

    def stamps(self) -> tuple[str, ...]:
        """Every backup that is complete — archive *and* manifest.

        A dump without its manifest is a run interrupted between the two
        writes. Reporting it would let the drill restore an archive whose
        digest list it cannot check.
        """
        if not self.dumps.is_dir():
            return ()
        found = [
            path.stem
            for path in self.dumps.glob("*.dump")
            if _STAMP.match(path.stem) and self.manifest_for(path.stem).is_file()
        ]
        return tuple(sorted(found))

    def newest(self) -> str | None:
        found = self.stamps()
        return found[-1] if found else None
