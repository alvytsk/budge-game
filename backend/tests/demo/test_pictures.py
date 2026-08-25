"""§G.4: Pictures are generated from stdlib and must be real PNGs.

`sniff` is the same detector that `POST /api/media` uses to decide whether to
store these bytes at all, so it is the oracle.
"""

import zlib

from budge.demo.pictures import PALETTE, solid_png
from budge.media.digest import digest_of, sniff


def test_a_generated_picture_is_a_png_the_media_route_will_accept() -> None:
    """Kills on: incorrect PNG signature — the demo would upload bytes that
    `POST /api/media` rejects with 415, and fail on the first step with no
    explanation.
    """
    assert sniff(solid_png((255, 0, 0))) == "image/png"


def test_different_colours_are_different_bytes() -> None:
    """§G.4: The stage screen must show that the frame has changed.

    Kills on: a generator that ignores colour — the demo would show the same
    picture throughout the bout, and there would be nothing to verify.
    """
    digests = {digest_of(solid_png(rgb)) for _, rgb in PALETTE}
    assert len(digests) == len(PALETTE)


def test_the_pixels_are_the_colour_that_was_asked_for() -> None:
    """The header can be valid while the content is garbage. Decompress and
    verify.

    PNG stores scanlines with a filter byte at the start of each; with filter
    0 the rest is RGB in sequence.
    """
    size = 4
    data = solid_png((10, 20, 30), size=size)
    idat = b""
    offset = 8
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        if kind == b"IDAT":
            idat += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(idat)
    assert len(raw) == size * (1 + size * 3)
    for row in range(size):
        line = raw[row * (1 + size * 3) : (row + 1) * (1 + size * 3)]
        assert line[0] == 0, "filter byte"
        assert line[1:] == bytes((10, 20, 30)) * size


def test_the_palette_names_its_colours_in_russian() -> None:
    """Colour names go into `answer_text`, which the host reads aloud. Names
    must be Russian Cyrillic so the host can pronounce them correctly to the
    room.
    """
    assert len(PALETTE) >= 8
    for name, rgb in PALETTE:
        assert name.strip() != ""
        assert all(0 <= channel <= 255 for channel in rgb)
        # Verify at least one Cyrillic character in each colour name
        assert any("Ѐ" <= char <= "ӿ" for char in name), (
            f"Colour name '{name}' is not in Russian Cyrillic"
        )


def test_chunk_crcs_are_computed_correctly() -> None:
    """Kills on: CRC computed over the wrong bytes — the PNG would render in
    no browser, but no other test would notice. The demo would show broken
    pictures in front of the room with no diagnostics.
    """
    # Generate a PNG with multiple chunks to verify CRC across different
    # payload sizes: IHDR (non-empty), IDAT (large), IEND (empty).
    data = solid_png((100, 150, 200), size=8)

    offset = 8  # Skip PNG signature
    chunks_verified = 0

    while offset < len(data):
        if offset + 12 > len(data):
            break

        # Read chunk: length (4), type (4), payload (length), CRC (4)
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        stored_crc = int.from_bytes(
            data[offset + 8 + length : offset + 12 + length], "big"
        )

        # Recompute CRC over type and payload only (not length field)
        computed_crc = zlib.crc32(kind + payload) & 0xFFFFFFFF

        assert stored_crc == computed_crc, (
            f"CRC mismatch in {kind.decode('ascii', errors='ignore')} chunk: "
            f"stored {stored_crc:08x}, computed {computed_crc:08x}"
        )

        chunks_verified += 1
        offset += 12 + length

    # Verify we found and checked the expected chunks
    assert chunks_verified >= 3, f"Expected at least 3 chunks, found {chunks_verified}"
