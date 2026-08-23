"""A `ContentDirectory` that answers from two dicts and remembers every
question.

The recording half is not a convenience. Ruling 1 makes «the stage
projection never *asks* for an unrevealed category» a property in its own
right — strictly stronger than filtering the answer on the way out — and
the only way to assert it is to read what was asked.
"""

from dataclasses import dataclass
from collections.abc import Mapping

from podvinsya.domain.ids import CategoryId, ImageId
from podvinsya.services.ports import ContentDescription


@dataclass(frozen=True, slots=True)
class Request:
    """One `describe` call, exactly as it arrived.

    Recorded per call rather than accumulated into a union: a test asserting
    "this id was never asked for" would pass against a union that had been
    filtered, and fail correctly against the call that actually asked.
    """

    categories: frozenset[CategoryId]
    images: frozenset[ImageId]


class RecordingContentDirectory:
    def __init__(
        self,
        category_names: Mapping[CategoryId, str] | None = None,
        image_answers: Mapping[ImageId, str] | None = None,
    ) -> None:
        self._category_names = dict(category_names or {})
        self._image_answers = dict(image_answers or {})
        self.requests: list[Request] = []

    def learn(self, category: CategoryId, name: str) -> None:
        """The administrator added the missing category. Used by the test
        that keeps `CachingContentDirectory` from caching a miss."""
        self._category_names[category] = name

    @property
    def asked_categories(self) -> frozenset[CategoryId]:
        """Every category id this directory was ever asked about."""
        return frozenset(c for request in self.requests for c in request.categories)

    @property
    def asked_images(self) -> frozenset[ImageId]:
        """Every image id this directory was ever asked about."""
        return frozenset(i for request in self.requests for i in request.images)

    async def describe(
        self, *, categories: frozenset[CategoryId], images: frozenset[ImageId]
    ) -> ContentDescription:
        self.requests.append(Request(categories=categories, images=images))
        return ContentDescription(
            category_names={
                c: self._category_names[c] for c in categories if c in self._category_names
            },
            image_answers={i: self._image_answers[i] for i in images if i in self._image_answers},
        )
