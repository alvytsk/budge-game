"""Display, and the two things it must not do."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.domain.ids import CategoryId, ImageId
from budge.library.catalogue import LibraryCatalogue
from budge.library.directory import DatabaseContentDirectory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

A_DIGEST = "a" * 64


async def a_category_with_an_image(
    sessions: async_sessionmaker[AsyncSession], title: str = "История"
) -> tuple[LibraryCatalogue, CategoryId, ImageId]:
    library = LibraryCatalogue(sessions)
    category = await library.create_category(title, is_secret=False)
    image = await library.add_image(
        category.id, media_sha256=A_DIGEST, answer_text="Гагарин"
    )
    return library, CategoryId(category.id), ImageId(image.id)


async def test_it_names_the_categories_it_was_asked_about(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    _library, category, _image = await a_category_with_an_image(sessions)

    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset({category}), images=frozenset()
    )

    assert described.category_names == {category: "История"}


async def test_it_answers_the_images_it_was_asked_about(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    _library, _category, image = await a_category_with_an_image(sessions)

    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset(), images=frozenset({image})
    )

    assert described.image_answers == {image: "Гагарин"}


async def test_it_says_nothing_about_what_it_was_not_asked(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The property plan 4's ruling 1 rests on.

    Kills on: returning every row in the table — the stage projection would
    then have every unrevealed name and every answer in scope, and its
    leak-proofness would fall back to depending on the frame layer alone."""
    library, category, _image = await a_category_with_an_image(sessions)
    other = await library.create_category("Кино", is_secret=False)
    await library.add_image(other.id, media_sha256=A_DIGEST, answer_text="Тарковский")

    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset({category}), images=frozenset()
    )

    assert set(described.category_names) == {category}
    assert described.image_answers == {}


async def test_an_unknown_id_is_absent_rather_than_null(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The contract plan 4 fixed: a missing key and a `None` value are the
    same fact, and carrying both would give every reader two branches for
    one state.

    Kills on: filling in `None` for what was not found, which ruling 3 of
    plan 4 turns into a visible content defect on one path and a silent
    one on the other."""
    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset({CategoryId(uuid4())}), images=frozenset({ImageId(uuid4())})
    )

    assert described.category_names == {}
    assert described.image_answers == {}


async def test_it_names_an_inactive_category(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§5.3: «Поздняя правка библиотеки не может задним числом изменить уже
    сыгранную дуэль.» A category switched off mid-match is still the
    category on the board.

    Kills on: filtering on `is_active` here — the group the room is looking
    at would go blank the moment an operator tidied the library."""
    library, category, _image = await a_category_with_an_image(sessions)
    await library.set_category_active(category, is_active=False)

    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset({category}), images=frozenset()
    )

    assert described.category_names == {category: "История"}


async def test_it_answers_for_an_inactive_image(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Same reason: the pack was drawn into `AttackDeclared` and is
    immutable.

    Kills on: filtering — the operator would lose the answer mid-duel for a
    picture that is on the screen in front of the room."""
    library, _category, image = await a_category_with_an_image(sessions)
    await library.set_image_active(image, is_active=False)

    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset(), images=frozenset({image})
    )

    assert described.image_answers == {image: "Гагарин"}


async def test_an_empty_request_touches_the_database_not_at_all(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame with no revealed groups is the whole of setup, and it must
    not cost two round trips per frame.

    Kills on: issuing `SELECT … WHERE id IN ()` unconditionally — the
    sessionmaker is replaced with one that raises, so any query at all
    fails this."""

    def refuse() -> AsyncSession:
        raise AssertionError("an empty request must not open a session")

    described = await DatabaseContentDirectory(refuse).describe(  # type: ignore[arg-type]
        categories=frozenset(), images=frozenset()
    )

    assert described.category_names == {}
    assert described.image_answers == {}


async def test_a_request_for_only_categories_does_not_query_images(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The stage projection always passes `images=frozenset()` (plan 4's
    ruling 1), so this is its every frame.

    Kills on: querying both tables regardless, which doubles the cost of
    the most common frame in the system and puts answers in scope for the
    one projection that must never have them."""
    _library, category, _image = await a_category_with_an_image(sessions)

    described = await DatabaseContentDirectory(sessions).describe(
        categories=frozenset({category}), images=frozenset()
    )

    assert described.image_answers == {}
