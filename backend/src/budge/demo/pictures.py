"""Solid-colour PNGs, written by hand out of the standard library.

§G.4: Pillow is not a dependency and will not become one for a demo. A PNG
of one flat colour is four chunks and a `zlib.compress`, which is less code
than the argument for adding an imaging library would be.

The colours differ on purpose. The demo exists so somebody can watch the
stage screen and see the picture change between judgements — a pack of
identical images would demonstrate the mechanic failing to be visible.
"""

import struct
import zlib

# Colour names go into `answer_text`, which the host reads aloud, so they are
# in Russian and nominative case — as a real answer would be read.
PALETTE: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("Красный", (220, 60, 50)),
    ("Синий", (45, 110, 220)),
    ("Зелёный", (60, 175, 90)),
    ("Жёлтый", (225, 190, 60)),
    ("Фиолетовый", (150, 95, 210)),
    ("Оранжевый", (235, 135, 55)),
    ("Бирюзовый", (60, 190, 190)),
    ("Розовый", (230, 110, 170)),
    ("Коричневый", (140, 100, 70)),
    ("Серый", (140, 145, 150)),
)

_SIGNATURE = b"\x89PNG\r\n\x1a\x0a"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, CRC over type and payload."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def solid_png(colour: tuple[int, int, int], size: int = 512) -> bytes:
    """A `size` x `size` square of one colour, as PNG bytes.

    Truecolour, 8 bits per channel, no alpha, no interlace — the simplest
    thing `sniff` accepts and every browser draws.
    """
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    # Filter byte 0 ("none") in front of every scanline: with one flat
    # colour there is nothing for a filter to predict, and 0 keeps the
    # bytes readable by anything that decompresses them.
    row = b"\x00" + bytes(colour) * size
    return (
        _SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(row * size, 9))
        + _chunk(b"IEND", b"")
    )
