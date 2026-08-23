"""A `ContentDirectory` that answers from two dicts and remembers every
question.

The recording half is not a convenience. Ruling 1 makes «the stage
projection never *asks* for an unrevealed category» a property in its own
right — strictly stronger than filtering the answer on the way out — and
the only way to assert it is to read what was asked.
"""

from dataclasses import dataclass
from collections.abc import Mapping

from budge.domain.ids import CategoryId, ImageId
from budge.services.ports import ContentDescription


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


class OverAnsweringContentDirectory:
    """Answers with everything it knows, whatever it was asked for.

    This exists to make §11's whole-tree frame test able to fail at all.

    Ruling 1 means `project_stage` never *asks* for an unrevealed
    category's name or for an image answer — so against an honest
    directory there is nothing in the projection's scope to leak, and the
    frame-level test passes no matter what the frame layer does. That is
    the design working; it is also a backstop that cannot fire.

    Driving the frame test with a directory that over-answers separates the
    two layers: ruling 1's own tests assert what was *asked*, and the
    whole-tree tests assert what the frame does when handed content it did
    not ask for. The second is not a hypothetical — over-answering is
    exactly what a caching or batching bug in plan 6's directory would look
    like from here.
    """

    def __init__(
        self,
        category_names: Mapping[CategoryId, str] | None = None,
        image_answers: Mapping[ImageId, str] | None = None,
    ) -> None:
        self._description = ContentDescription(
            category_names=dict(category_names or {}),
            image_answers=dict(image_answers or {}),
        )
        self.requests: list[Request] = []

    async def describe(
        self, *, categories: frozenset[CategoryId], images: frozenset[ImageId]
    ) -> ContentDescription:
        self.requests.append(Request(categories=categories, images=images))
        return self._description
