"""Display: plan 4's `ContentDirectory`, over the real library.

Two properties matter here, and both are about what this does *not* do.

It answers exactly the question it was asked. Plan 4's ruling 1 makes the
stage projection leak-proof by never *asking* for an unrevealed category's
name or for an image answer — a directory that returned more than it was
asked for would put those back into the projection's scope and reduce that
ruling to a promise about the frame layer alone.

It does not filter on `is_active`. §5.3: «Поздняя правка библиотеки не
может задним числом изменить уже сыгранную дуэль.» A category deactivated
mid-match is still the category on the board, and an image deactivated
mid-duel is still the picture the room is looking at — both were written
into the log when the board was dealt and the pack was drawn. Filtering
here would blank a group the room can see, or leave the operator with no
answer for a picture that is on the screen. Filtering belongs to selection,
which is a different question asked at a different time.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.db.models import Category, Image
from budge.domain.ids import CategoryId, ImageId
from budge.services.ports import ContentDescription


class DatabaseContentDirectory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def describe(
        self, *, categories: frozenset[CategoryId], images: frozenset[ImageId]
    ) -> ContentDescription:
        """One session, and a query only for a set that has something in it.

        A frame with no revealed groups is the common case throughout setup,
        and `SELECT … WHERE id IN ()` twice per frame is two round trips to
        learn nothing.
        """
        if not categories and not images:
            return ContentDescription()

        names: dict[CategoryId, str] = {}
        answers: dict[ImageId, str] = {}
        async with self._sessions() as session:
            if categories:
                rows = (
                    await session.execute(
                        select(Category.id, Category.title).where(
                            Category.id.in_(list(categories))
                        )
                    )
                ).all()
                names = {CategoryId(row_id): title for row_id, title in rows}
            if images:
                answered = (
                    await session.execute(
                        select(Image.id, Image.answer_text).where(Image.id.in_(list(images)))
                    )
                ).all()
                answers = {ImageId(row_id): answer for row_id, answer in answered}
        # An id the library cannot resolve is simply absent, never present
        # with a placeholder — the contract `ContentDescription` fixes.
        return ContentDescription(category_names=names, image_answers=answers)
