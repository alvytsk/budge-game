"""What the one writer does, against a real database.

The structural half of §5.3's invariant — that this is the *only* writer —
lives in `test_write_paths.py`, which reads source rather than rows.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Category, Image
from podvinsya.library.catalogue import LibraryCatalogue, UnknownCategory, UnknownImage

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

A_DIGEST = "a" * 64
ANOTHER_DIGEST = "b" * 64


def catalogue(sessions: async_sessionmaker[AsyncSession]) -> LibraryCatalogue:
    return LibraryCatalogue(sessions)


async def version_of(
    sessions: async_sessionmaker[AsyncSession], category_id: object
) -> int:
    async with sessions() as session:
        return (
            await session.execute(select(Category.version).where(Category.id == category_id))
        ).scalar_one()


async def test_renaming_a_category_bumps_its_version(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    assert category.version == 1

    await library.edit_category(category.id, title="История СССР", is_secret=False)

    assert await version_of(sessions, category.id) == 2


async def test_changing_is_secret_bumps_its_version(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = catalogue(sessions)
    category = await library.create_category("Мой секрет", is_secret=False)

    await library.edit_category(category.id, title="Мой секрет", is_secret=True)

    assert await version_of(sessions, category.id) == 2


async def test_adding_an_image_bumps_the_category_version(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§5.3's whole point: «путь редактирования, меняющий картинки без
    бампа версии, проскользнёт мимо блокировки»."""
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)

    await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")

    assert await version_of(sessions, category.id) == 2


async def test_editing_an_image_bumps_the_category_version(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    image = await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")

    await library.edit_image(image.id, media_sha256=ANOTHER_DIGEST, answer_text="Титов")

    assert await version_of(sessions, category.id) == 3


async def test_deactivating_an_image_bumps_the_category_version(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    image = await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")

    await library.set_image_active(image.id, is_active=False)

    assert await version_of(sessions, category.id) == 3


async def test_reordering_images_bumps_the_category_version(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    first = await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")
    second = await library.add_image(
        category.id, media_sha256=ANOTHER_DIGEST, answer_text="Титов"
    )

    await library.reorder_images(category.id, [second.id, first.id])

    assert await version_of(sessions, category.id) == 4
    detail = await library.category_detail(category.id)
    assert [image.id for image in detail.images] == [second.id, first.id]


async def test_deactivating_the_category_itself_does_not_bump(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling 5: deactivation does not change what the category *is*, and
    selection filters on `is_active` at read time — so a flip cannot
    invalidate a selection that has already locked the row.

    Kills on: bumping on every write indiscriminately, which makes the
    version meaningless as a signal and hides a genuinely missing bump."""
    library = catalogue(sessions)
    category = await library.create_category("Аниме", is_secret=False)

    await library.set_category_active(category.id, is_active=False)

    assert await version_of(sessions, category.id) == 1


async def test_nothing_is_ever_hard_deleted(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§5.3's «мягко», and the observed behaviour behind it: the operator
    turned «аниме» off after three editions rather than throwing it away,
    which is a decision expected to be reversed.

    Kills on: implementing deactivation as a DELETE, which makes
    «выключить, не удалять» irreversible and takes the images with it."""
    library = catalogue(sessions)
    category = await library.create_category("Аниме", is_secret=False)
    await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")

    await library.set_category_active(category.id, is_active=False)

    async with sessions() as session:
        assert (
            await session.execute(select(Category).where(Category.id == category.id))
        ).scalar_one_or_none() is not None
        assert len((await session.execute(select(Image))).scalars().all()) == 1


async def test_deactivating_a_category_leaves_its_images_active(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling 6: reactivation restores exactly what was there.

    Kills on: cascading the flag to the images, which would make the round
    trip lossy — the operator turns a theme back on and finds it empty."""
    library = catalogue(sessions)
    category = await library.create_category("Аниме", is_secret=False)
    await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")

    await library.set_category_active(category.id, is_active=False)
    await library.set_category_active(category.id, is_active=True)

    detail = await library.category_detail(category.id)
    assert [image.is_active for image in detail.images] == [True]


async def test_an_unknown_category_is_reported_not_ignored(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: an UPDATE matching zero rows returning quietly, which
    turns a mistyped id in an admin request into a silent success — the
    operator believes they renamed something."""
    library = catalogue(sessions)
    with pytest.raises(UnknownCategory):
        await library.edit_category(uuid4(), title="x", is_secret=False)
    with pytest.raises(UnknownCategory):
        await library.set_category_active(uuid4(), is_active=False)
    with pytest.raises(UnknownCategory):
        await library.add_image(uuid4(), media_sha256=A_DIGEST, answer_text="x")
    with pytest.raises(UnknownCategory):
        await library.category_detail(uuid4())


async def test_an_unknown_image_is_reported_not_ignored(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = catalogue(sessions)
    with pytest.raises(UnknownImage):
        await library.edit_image(uuid4(), media_sha256=A_DIGEST, answer_text="x")
    with pytest.raises(UnknownImage):
        await library.set_image_active(uuid4(), is_active=False)


async def test_reordering_must_name_exactly_the_category_s_images(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A partial reorder would leave two images sharing a position, and
    `position` is what the admin screen renders by.

    Kills on: accepting a subset and renumbering only those, which silently
    reorders images the operator did not name."""
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    first = await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")
    await library.add_image(category.id, media_sha256=ANOTHER_DIGEST, answer_text="Титов")

    with pytest.raises(UnknownImage):
        await library.reorder_images(category.id, [first.id])


async def test_new_images_are_appended_in_order(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: always writing position 0, which makes the admin screen's
    order arbitrary and the reorder endpoint meaningless."""
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    for index in range(3):
        await library.add_image(
            category.id, media_sha256=A_DIGEST, answer_text=f"answer {index}"
        )

    detail = await library.category_detail(category.id)
    assert [image.position for image in detail.images] == [0, 1, 2]


async def test_the_listing_counts_only_active_images(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§8's threshold warning is read off this number, so counting inactive
    images would report a category as stocked when it is not.

    Kills on: `count(*)` without the `is_active` filter."""
    library = catalogue(sessions)
    category = await library.create_category("История", is_secret=False)
    kept = await library.add_image(category.id, media_sha256=A_DIGEST, answer_text="Гагарин")
    gone = await library.add_image(
        category.id, media_sha256=ANOTHER_DIGEST, answer_text="Титов"
    )
    await library.set_image_active(gone.id, is_active=False)

    listed = {row.id: row for row in await library.list_categories()}
    assert listed[category.id].active_image_count == 1
    assert kept.id != gone.id


async def test_the_listing_shows_inactive_categories_too(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """An operator who turned a theme off has to be able to turn it back
    on, which means seeing it.

    Kills on: filtering the admin listing by `is_active` — the only way
    back would be a database client."""
    library = catalogue(sessions)
    category = await library.create_category("Аниме", is_secret=False)
    await library.set_category_active(category.id, is_active=False)

    assert [row.id for row in await library.list_categories()] == [category.id]
