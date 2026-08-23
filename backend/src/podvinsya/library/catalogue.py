"""The only thing that writes to the library.

§5.3 makes that a structural requirement rather than a convention:

    Каждая семантическая правка категории бампает `categories.version`.
    Это не бухгалтерия, а инвариант блокировки: отбор категорий на партию
    берёт `FOR SHARE` на родительскую строку, и путь редактирования,
    меняющий картинки без бампа версии, проскользнёт мимо блокировки.
    Бамп обеспечивается ровно в одном месте, и это покрыто тестом.

So every write in the whole application lives in this module, the bump
lives in `_bump`, and `test_no_write_outside_the_catalogue` parses the
source tree to keep both true.

Nothing here deletes. §5.3: «контент удаляется только мягко, флагом
`is_active`» — which is also the observed behaviour it was drawn from, an
operator who turned a theme off after three editions rather than throwing
it away, expecting to turn it back on.
"""

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Category, Image

# What ruling 5 counts as a semantic edit, enumerated rather than inferred:
# §5.3 does not define the term, and a definition left implicit is one that
# each new edit path gets to decide for itself.
SEMANTIC_EDITS = (
    "a category's title or is_secret flag; an image added, edited, "
    "deactivated, reactivated or reordered"
)


class UnknownCategory(Exception):
    """No category with that id. Raised rather than ignored: an UPDATE
    matching zero rows would otherwise turn a mistyped id in an admin
    request into a silent success."""


class UnknownImage(Exception):
    """No image with that id, or an image outside the category it was
    named under."""


@dataclass(frozen=True, slots=True)
class CategoryRow:
    """One category as the admin listing needs it.

    `active_image_count` is what §8's «мягкое предупреждение, если у
    какой-то из них картинок меньше настраиваемого порога» is read off, so
    it counts active images only — an inactive one cannot be drawn.
    """

    id: UUID
    title: str
    is_secret: bool
    is_active: bool
    version: int
    active_image_count: int


@dataclass(frozen=True, slots=True)
class ImageRow:
    id: UUID
    media_sha256: str
    answer_text: str
    position: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class CategoryDetail:
    category: CategoryRow
    images: tuple[ImageRow, ...]


class LibraryCatalogue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # ------------------------------------------------------------------
    # The bump, and the only place it lives.
    # ------------------------------------------------------------------

    async def _bump(self, session: AsyncSession, category_id: UUID) -> None:
        """§5.3's locking invariant, in the one place ruling 5 puts it.

        A database-side increment rather than a read-modify-write (ruling
        4): the latter needs the row to have been read first, and the edit
        paths that do not read it are exactly the ones §5.3 says will slip
        past the `FOR SHARE` lock.

        What counts as a semantic edit is `SEMANTIC_EDITS` above. Toggling
        the category's own `is_active` is deliberately not one — see the
        plan's ruling 5.
        """
        result = await session.execute(
            update(Category)
            .where(Category.id == category_id)
            .values(version=Category.version + 1)
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise UnknownCategory(str(category_id))

    async def _category_id_of(self, session: AsyncSession, image_id: UUID) -> UUID:
        found = (
            await session.execute(select(Image.category_id).where(Image.id == image_id))
        ).scalar_one_or_none()
        if found is None:
            raise UnknownImage(str(image_id))
        return UUID(str(found))

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def create_category(self, title: str, *, is_secret: bool) -> CategoryRow:
        """A new category starts at version 1, not 0: the first edit must
        be distinguishable from never having been edited."""
        category_id = UUID(str(uuid4()))
        async with self._sessions() as session, session.begin():
            session.add(
                Category(
                    id=category_id,
                    title=title,
                    is_secret=is_secret,
                    is_active=True,
                    version=1,
                )
            )
        return CategoryRow(
            id=category_id,
            title=title,
            is_secret=is_secret,
            is_active=True,
            version=1,
            active_image_count=0,
        )

    async def edit_category(self, category_id: UUID, *, title: str, is_secret: bool) -> None:
        """A semantic edit: it changes what the category *is*, so it bumps."""
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(Category)
                .where(Category.id == category_id)
                .values(title=title, is_secret=is_secret)
            )
            if cast("CursorResult[Any]", result).rowcount == 0:
                raise UnknownCategory(str(category_id))
            await self._bump(session, category_id)

    async def set_category_active(self, category_id: UUID, *, is_active: bool) -> None:
        """Soft delete, and soft undelete (§5.3).

        Deliberately not a semantic edit (ruling 5): deactivation does not
        change what the category is, and selection filters on `is_active`
        at read time, so a flip cannot invalidate a selection that has
        already locked the row. It also leaves the images alone, so
        reactivating restores exactly what was there.
        """
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(Category).where(Category.id == category_id).values(is_active=is_active)
            )
            if cast("CursorResult[Any]", result).rowcount == 0:
                raise UnknownCategory(str(category_id))

    # ------------------------------------------------------------------
    # Images. Every one of these bumps the parent — that is §5.3's point.
    # ------------------------------------------------------------------

    async def add_image(
        self, category_id: UUID, *, media_sha256: str, answer_text: str
    ) -> ImageRow:
        image_id = UUID(str(uuid4()))
        async with self._sessions() as session, session.begin():
            # Bump first: it is also the existence check, so a category id
            # that is not real fails before a row is inserted against it.
            await self._bump(session, category_id)
            next_position = (
                await session.execute(
                    select(func.coalesce(func.max(Image.position) + 1, 0)).where(
                        Image.category_id == category_id
                    )
                )
            ).scalar_one()
            session.add(
                Image(
                    id=image_id,
                    category_id=category_id,
                    media_sha256=media_sha256,
                    answer_text=answer_text,
                    position=next_position,
                    is_active=True,
                )
            )
        return ImageRow(
            id=image_id,
            media_sha256=media_sha256,
            answer_text=answer_text,
            position=int(next_position),
            is_active=True,
        )

    async def edit_image(self, image_id: UUID, *, media_sha256: str, answer_text: str) -> None:
        async with self._sessions() as session, session.begin():
            category_id = await self._category_id_of(session, image_id)
            await session.execute(
                update(Image)
                .where(Image.id == image_id)
                .values(media_sha256=media_sha256, answer_text=answer_text)
            )
            await self._bump(session, category_id)

    async def set_image_active(self, image_id: UUID, *, is_active: bool) -> None:
        """Soft delete for an image — and, unlike a category's flag, a
        semantic edit: it changes what can be drawn from the category, and
        that is exactly what §5.3's lock protects."""
        async with self._sessions() as session, session.begin():
            category_id = await self._category_id_of(session, image_id)
            await session.execute(
                update(Image).where(Image.id == image_id).values(is_active=is_active)
            )
            await self._bump(session, category_id)

    async def reorder_images(self, category_id: UUID, order: list[UUID]) -> None:
        """Assign positions 0..n-1 in the given order.

        The list must name every image the category has, active or not: a
        partial reorder would leave two images sharing a position, and
        `position` is what the admin screen renders by.
        """
        async with self._sessions() as session, session.begin():
            existing = set(
                (
                    await session.execute(
                        select(Image.id).where(Image.category_id == category_id)
                    )
                )
                .scalars()
                .all()
            )
            if not existing and order:
                raise UnknownCategory(str(category_id))
            if set(order) != existing or len(order) != len(existing):
                raise UnknownImage(
                    f"the order must name every image of {category_id} exactly once"
                )
            for position, image_id in enumerate(order):
                await session.execute(
                    update(Image).where(Image.id == image_id).values(position=position)
                )
            await self._bump(session, category_id)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_categories(self) -> tuple[CategoryRow, ...]:
        """Every category, active or not.

        Filtering by `is_active` here would leave an operator who turned a
        theme off with no way back short of a database client.
        """
        active_images = (
            select(Image.category_id, func.count().label("n"))
            .where(Image.is_active.is_(True))
            .group_by(Image.category_id)
            .subquery()
        )
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(Category, func.coalesce(active_images.c.n, 0))
                    .outerjoin(active_images, active_images.c.category_id == Category.id)
                    .order_by(Category.title)
                )
            ).all()
        return tuple(
            CategoryRow(
                id=UUID(str(category.id)),
                title=category.title,
                is_secret=category.is_secret,
                is_active=category.is_active,
                version=category.version,
                active_image_count=int(count),
            )
            for category, count in rows
        )

    async def category_detail(self, category_id: UUID) -> CategoryDetail:
        listed = {row.id: row for row in await self.list_categories()}
        if category_id not in listed:
            raise UnknownCategory(str(category_id))
        async with self._sessions() as session:
            images = (
                (
                    await session.execute(
                        select(Image)
                        .where(Image.category_id == category_id)
                        .order_by(Image.position)
                    )
                )
                .scalars()
                .all()
            )
        return CategoryDetail(
            category=listed[category_id],
            images=tuple(
                ImageRow(
                    id=UUID(str(image.id)),
                    media_sha256=image.media_sha256,
                    answer_text=image.answer_text,
                    position=image.position,
                    is_active=image.is_active,
                )
                for image in images
            ),
        )
