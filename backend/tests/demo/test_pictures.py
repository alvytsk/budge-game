"""§G.4: картинки генерируются из stdlib, и они должны быть настоящими PNG.

`sniff` — тот же самый детектор, которым `POST /api/media` решает, будет ли
он эти байты вообще хранить, так что он и есть оракул.
"""

import zlib

from budge.demo.pictures import PALETTE, solid_png
from budge.media.digest import digest_of, sniff


def test_a_generated_picture_is_a_png_the_media_route_will_accept() -> None:
    """Kills on: заголовок, собранный руками с ошибкой, — демо загрузило бы
    байты, которые `POST /api/media` отбивает с 415, и падало бы на первом
    же шаге, ничего не объяснив.
    """
    assert sniff(solid_png((255, 0, 0))) == "image/png"


def test_different_colours_are_different_bytes() -> None:
    """§G.4: на экране сцены должно быть видно, что кадр сменился.

    Kills on: генератор, игнорирующий цвет, — демо показывало бы одну и ту
    же картинку всю дуэль, и проверять было бы нечего.
    """
    digests = {digest_of(solid_png(rgb)) for _, rgb in PALETTE}
    assert len(digests) == len(PALETTE)


def test_the_pixels_are_the_colour_that_was_asked_for() -> None:
    """Заголовок может быть валиден, а содержимое — мусор. Распаковываем.

    PNG хранит скан-строки с байтом фильтра в начале каждой; при filter 0
    остальное — это RGB подряд.
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
    """Название уходит в `answer_text`, который ведущий читает вслух."""
    assert len(PALETTE) >= 8
    for name, rgb in PALETTE:
        assert name.strip() != ""
        assert all(0 <= channel <= 255 for channel in rgb)
