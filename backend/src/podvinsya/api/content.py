"""Two `ContentDirectory` implementations: one that knows nothing, and one
that remembers.

Plan 6 supplies the database-backed directory. Until it does, the server
still has to run — and ruling 3 already decided what a nameless category
means on each surface: `hidden` to the stage, `null` to the host. So the
null implementation is not a stub that has to be replaced before anything
works; it is a directory whose answer happens to be empty, and both
projections were designed against exactly that answer.
"""

import logging

from podvinsya.domain.ids import CategoryId, ImageId
from podvinsya.services.ports import (
    ContentDescription,
    ContentDirectory,
    ContentExhausted,
    Transaction,
)

logger = logging.getLogger(__name__)


class UnavailableContent:
    """Names nothing and answers nothing.

    It returns an empty description rather than raising: a directory that
    raised would take down the projection, which would take down the
    subscriber's writer task, which would take a live match off the air —
    all for a content gap §8 calls an administrator's problem, not a
    domain transition.
    """

    async def describe(
        self, *, categories: frozenset[CategoryId], images: frozenset[ImageId]
    ) -> ContentDescription:
        return ContentDescription()


class CachingContentDirectory:
    """Remembers every id it has ever resolved, and asks only for the rest.

    Unbounded on purpose. §8 makes the library permanent — «библиотека
    постоянная и переиспользуется между выпусками» — so a name read once
    cannot become wrong, and one match touches at most one id per cell plus
    one per image drawn. An eviction policy here would buy nothing and
    would have to be reasoned about on every frame.

    A miss is *not* cached. An id the library could not resolve today is
    the content defect ruling 3 puts in front of the operator, and it is
    the one thing about the library that genuinely changes: caching the
    absence would make a category added after the first miss permanently
    nameless for the life of the process.
    """

    def __init__(self, delegate: ContentDirectory) -> None:
        self._delegate = delegate
        self._category_names: dict[CategoryId, str] = {}
        self._image_answers: dict[ImageId, str] = {}

    async def describe(
        self, *, categories: frozenset[CategoryId], images: frozenset[ImageId]
    ) -> ContentDescription:
        missing_categories = categories - self._category_names.keys()
        missing_images = images - self._image_answers.keys()
        if missing_categories or missing_images:
            fresh = await self._delegate.describe(
                categories=missing_categories, images=missing_images
            )
            self._category_names.update(fresh.category_names)
            self._image_answers.update(fresh.image_answers)
        return ContentDescription(
            category_names={
                category: self._category_names[category]
                for category in categories
                if category in self._category_names
            },
            image_answers={
                image: self._image_answers[image]
                for image in images
                if image in self._image_answers
            },
        )


class UnavailableCategories:
    """The `CategoryBank` plan 6 has not written yet.

    Every draw raises `ContentExhausted`, which §6.3 already routes as an
    ordinary rejection rather than a quarantine — so a `DealBoard` against
    a server with no library refuses cleanly and the operator is told
    plainly (§8: «рантайм отдаёт ведущему явную ошибку»). Raising anything
    else here would take the match off the air for a content gap §8 calls
    an administrator's problem.
    """

    async def draw_categories(
        self, tx: Transaction, count: int, *, exclude: frozenset[CategoryId]
    ) -> tuple[CategoryId, ...]:
        raise ContentExhausted(
            f"asked for {count} categories: no content library is configured (plan 6)"
        )

    async def draw_images(
        self, tx: Transaction, category: CategoryId, count: int
    ) -> tuple[ImageId, ...]:
        raise ContentExhausted(
            f"asked for {count} images of {category}: no content library is configured (plan 6)"
        )
