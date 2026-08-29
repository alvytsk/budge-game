"""What a media address is, and what may live at one.

§7.6: «Медиа контент-адресуемо по sha256». The address *is* the content, so
it is computed here and nowhere else — a digest that arrived from a client
is a name it chose, and two different pictures could then claim one
address, which is the single property content addressing exists to give.

`sniff` answers both questions this module is asked: at upload, whether
these bytes are something this system will serve at all, and at serve time,
what to call them. One function for both, so the two answers cannot
disagree (ruling 7).
"""

import hashlib
from collections.abc import Mapping

# Raster formats only. §9.3 puts both surfaces in one Vite application, so
# anything served from here is served from the console's own origin — and an
# SVG is a document that can carry script, which would then run there.
# Refused by not appearing in this table (ruling 6).
ACCEPTED_TYPES: Mapping[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

# WebP and AVIF are RIFF and ISO-BMFF containers: their marker is not at
# offset zero, so they are matched separately rather than bent into a table
# keyed by prefix.
_RIFF = b"RIFF"
_WEBP = b"WEBP"
_FTYP = b"ftyp"
_AVIF_BRANDS = (b"avif", b"avis")

SNIFFED_TYPES = (*ACCEPTED_TYPES, "image/webp", "image/avif")


def digest_of(data: bytes) -> str:
    """Lowercase hex sha256 — the shape `ck_images_media_sha256_is_a_digest`
    enforces in the schema and plan 6's Pydantic pattern enforces at the
    edge."""
    return hashlib.sha256(data).hexdigest()


def sniff(data: bytes) -> str | None:
    """The content type these bytes actually are, or `None` for anything
    this system will not serve.

    Sniffed rather than taken from the request: a content type a client
    supplied is a claim, and what this endpoint stores it later serves back
    to the room's browser.

    Every comparison is a slice, never an index — a two-byte upload is a
    malformed request, and it must produce a 415 rather than an
    `IndexError` on the way to a 500.
    """
    for content_type, markers in ACCEPTED_TYPES.items():
        if any(data.startswith(marker) for marker in markers):
            return content_type
    if data[:4] == _RIFF and data[8:12] == _WEBP:
        return "image/webp"
    if data[4:8] == _FTYP and data[8:12] in _AVIF_BRANDS:
        return "image/avif"
    return None
