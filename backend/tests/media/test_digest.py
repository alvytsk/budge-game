"""The address, and what may live at one. No I/O, no HTTP, no container."""

import hashlib
import re

import pytest

from budge.media.digest import ACCEPTED_TYPES, SNIFFED_TYPES, digest_of, sniff

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF87 = b"GIF87a" + b"\x00" * 32
GIF89 = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
AVIF = b"\x00\x00\x00\x20" + b"ftyp" + b"avif" + b"\x00" * 32

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><html><body><script>alert(1)</script></body></html>"

DIGEST_SHAPE = re.compile(r"^[0-9a-f]{64}$")


def test_the_digest_is_the_sha256_of_the_bytes() -> None:
    """Kills on: hashing anything but the bytes — a filename, a salt, a
    length prefix. The check constraint on `images.media_sha256` and plan
    6's Pydantic pattern would both still pass, and the address would
    simply have stopped being a content address."""
    assert digest_of(b"hello") == hashlib.sha256(b"hello").hexdigest()


def test_identical_bytes_have_one_address() -> None:
    assert digest_of(PNG) == digest_of(bytes(PNG))


def test_different_bytes_have_different_addresses() -> None:
    assert digest_of(PNG) != digest_of(JPEG)


def test_a_digest_matches_the_shape_the_database_enforces() -> None:
    """64 lowercase hex characters — what `ck_images_media_sha256_is_a_digest`
    and plan 6's Pydantic pattern both require.

    Kills on: switching to `sha256(...).digest().hex().upper()` or to a
    different hash — the failure would otherwise first appear as a check
    constraint violation on an INSERT, far from its cause."""
    assert DIGEST_SHAPE.match(digest_of(b""))
    assert DIGEST_SHAPE.match(digest_of(PNG))


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF87, "image/gif"),
        (GIF89, "image/gif"),
        (WEBP, "image/webp"),
        (AVIF, "image/avif"),
    ],
    ids=["png", "jpeg", "gif87", "gif89", "webp", "avif"],
)
def test_every_accepted_type_is_sniffed(sample: bytes, expected: str) -> None:
    assert sniff(sample) == expected


def test_an_svg_is_not_an_accepted_type() -> None:
    """Ruling 6, by name.

    Kills on: adding `image/svg+xml` to the table. §9.3 puts both surfaces
    in one Vite application, so an SVG fetched by `<img>` from this
    endpoint is served from the console's own origin — and an SVG is a
    document that can carry script, which would then run there."""
    assert sniff(SVG) is None
    assert "image/svg+xml" not in SNIFFED_TYPES


def test_html_is_not_an_accepted_type() -> None:
    assert sniff(HTML) is None


def test_empty_bytes_are_not_an_accepted_type() -> None:
    assert sniff(b"") is None


@pytest.mark.parametrize("length", [0, 1, 2, 3, 7, 11], ids=str)
def test_a_truncated_header_does_not_crash_the_sniffer(length: int) -> None:
    """Kills on: indexing rather than slicing — `data[8:12]` on a two-byte
    upload is `b""`, but `data[8]` is an `IndexError`, and a malformed
    upload would reach the operator as a 500 instead of a 415."""
    assert sniff(PNG[:length]) in (None, "image/png")
    assert sniff(WEBP[:length]) is None
    assert sniff(AVIF[:length]) is None


def test_a_riff_that_is_not_a_webp_is_refused() -> None:
    """A `.wav` is a RIFF too. Kills on: matching the container and not the
    form — audio would be accepted, stored, and served to an `<img>`."""
    wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 32
    assert sniff(wav) is None


def test_an_ftyp_that_is_not_an_avif_is_refused() -> None:
    """An MP4 is ISO-BMFF too, and its brand is what tells them apart."""
    mp4 = b"\x00\x00\x00\x20" + b"ftyp" + b"isom" + b"\x00" * 32
    assert sniff(mp4) is None


def test_the_accepted_table_and_the_sniffed_list_agree() -> None:
    """`SNIFFED_TYPES` is what the tests and the API's error message read;
    a type in the table but not the list, or the reverse, would make one of
    them describe a set the sniffer does not implement."""
    assert set(ACCEPTED_TYPES) <= set(SNIFFED_TYPES)
    assert set(SNIFFED_TYPES) - set(ACCEPTED_TYPES) == {"image/webp", "image/avif"}
