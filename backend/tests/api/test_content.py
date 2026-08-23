"""The two directories, and the one thing the cache must not remember."""

from uuid import uuid4

from podvinsya.api.content import CachingContentDirectory, UnavailableContent
from podvinsya.domain.ids import CategoryId, ImageId
from support.content import RecordingContentDirectory, Request

HISTORY = CategoryId(uuid4())
CINEMA = CategoryId(uuid4())
AN_IMAGE = ImageId(uuid4())

NAMES = {HISTORY: "История", CINEMA: "Кино"}
ANSWERS = {AN_IMAGE: "Гагарин"}


async def test_a_directory_that_knows_nothing_answers_with_nothing() -> None:
    """Kills on: `UnavailableContent.describe` raising. A raise would reach
    the subscriber's writer task, which would take a live match off the air
    for a content gap §8 calls an administrator's problem."""
    described = await UnavailableContent().describe(
        categories=frozenset({HISTORY}), images=frozenset({AN_IMAGE})
    )
    assert described.category_names == {}
    assert described.image_answers == {}


async def test_the_cache_asks_the_delegate_once_per_id() -> None:
    """Kills on: caching the call rather than the ids, which would re-query
    for any set that is not byte-identical to a previous one."""
    delegate = RecordingContentDirectory(NAMES, ANSWERS)
    cache = CachingContentDirectory(delegate)

    first = await cache.describe(categories=frozenset({HISTORY}), images=frozenset())
    second = await cache.describe(
        categories=frozenset({HISTORY, CINEMA}), images=frozenset()
    )

    assert first.category_names == {HISTORY: "История"}
    assert second.category_names == {HISTORY: "История", CINEMA: "Кино"}
    assert [r.categories for r in delegate.requests] == [
        frozenset({HISTORY}),
        frozenset({CINEMA}),
    ]


async def test_the_cache_asks_for_nothing_when_it_already_knows_everything() -> None:
    """Kills on: always delegating. The second call must not reach the
    delegate at all — not with an empty set, not with anything."""
    delegate = RecordingContentDirectory(NAMES, ANSWERS)
    cache = CachingContentDirectory(delegate)

    await cache.describe(categories=frozenset({HISTORY}), images=frozenset({AN_IMAGE}))
    await cache.describe(categories=frozenset({HISTORY}), images=frozenset({AN_IMAGE}))

    assert len(delegate.requests) == 1


async def test_the_cache_asks_only_for_what_it_is_missing() -> None:
    """Kills on: passing the whole requested set down when one id is new."""
    delegate = RecordingContentDirectory(NAMES, ANSWERS)
    cache = CachingContentDirectory(delegate)

    await cache.describe(categories=frozenset({HISTORY}), images=frozenset({AN_IMAGE}))
    await cache.describe(categories=frozenset({HISTORY, CINEMA}), images=frozenset({AN_IMAGE}))

    assert delegate.requests[1] == Request(categories=frozenset({CINEMA}), images=frozenset())


async def test_an_id_the_library_cannot_resolve_stays_unresolved() -> None:
    """Kills on: caching a miss. A category added to the library after the
    first miss would otherwise stay nameless for the life of the process —
    and ruling 3 makes a nameless category a visible content defect, so it
    is exactly the thing an operator fixes and re-checks."""
    unknown = CategoryId(uuid4())
    delegate = RecordingContentDirectory(NAMES, ANSWERS)
    cache = CachingContentDirectory(delegate)

    first = await cache.describe(categories=frozenset({unknown}), images=frozenset())
    assert unknown not in first.category_names

    delegate.learn(unknown, "Появилась")  # the administrator fixed it
    second = await cache.describe(categories=frozenset({unknown}), images=frozenset())
    assert second.category_names == {unknown: "Появилась"}


async def test_the_cache_answers_only_what_it_was_asked_for() -> None:
    """Kills on: returning the whole cache. A host frame asking for one
    image's answer must not receive every answer the process has ever seen —
    the projection would then have content in scope that it never requested,
    which is the leak ruling 1 exists to make impossible."""
    delegate = RecordingContentDirectory(NAMES, ANSWERS)
    cache = CachingContentDirectory(delegate)

    await cache.describe(categories=frozenset({HISTORY, CINEMA}), images=frozenset())
    narrowed = await cache.describe(categories=frozenset({HISTORY}), images=frozenset())

    assert narrowed.category_names == {HISTORY: "История"}
