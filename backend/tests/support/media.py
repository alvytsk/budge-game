"""A `MediaStore` that keeps everything in a dict, and can be told to fail.

The `fail` switch is not a convenience: §10's health check and the 503 path
on `GET /api/media/{digest}` are both about a store that is *unreachable*,
which is a different state from a store that is empty — and one no fake
without a switch can produce.
"""

from podvinsya.media.digest import digest_of
from podvinsya.services.ports import MediaUnavailable


class InMemoryMediaStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = fail
        self.puts = 0
        # Reads are counted for the backup suite: I6 says a mirrored blob
        # is never fetched twice, and only a counter can show that.
        self.gets = 0

    def _check(self) -> None:
        if self.fail:
            raise MediaUnavailable("the object store is unreachable")

    async def put(self, data: bytes) -> str:
        self._check()
        digest = digest_of(data)
        if digest not in self.objects:
            # Ruling 3: identical bytes are already there, byte for byte.
            self.objects[digest] = data
            self.puts += 1
        return digest

    async def get(self, digest: str) -> bytes | None:
        self._check()
        self.gets += 1
        return self.objects.get(digest)

    async def exists(self, digest: str) -> bool:
        self._check()
        return digest in self.objects

    async def healthy(self) -> bool:
        return not self.fail
