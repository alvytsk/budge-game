"""Selection, against a real library and a real PostgreSQL.

The `FOR SHARE` test is the one that needs the real database: a lock is
not observable from inside the transaction that took it, so it is asserted
the only way it can be — by watching a second transaction block on it.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Category
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.ids import CategoryId
from podvinsya.library.bank import DatabaseCategoryBank
from podvinsya.library.catalogue import LibraryCatalogue
from podvinsya.services.ports import ContentExhausted
from support.db import wait_until_a_backend_is_blocked_on

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

A_DIGEST = "a" * 64


async def stock(
    sessions: async_sessionmaker[AsyncSession],
    *,
    ordinary: int = 0,
    secrets: int = 0,
    images_each: int = 3,
) -> LibraryCatalogue:
    library = LibraryCatalogue(sessions)
    for index in range(ordinary):
        category = await library.create_category(f"Тема {index}", is_secret=False)
        for image in range(images_each):
            await library.add_image(
                category.id, media_sha256=A_DIGEST, answer_text=f"{index}-{image}"
            )
    for index in range(secrets):
        secret = await library.create_category(f"Секрет {index}", is_secret=True)
        for image in range(images_each):
            await library.add_image(
                secret.id, media_sha256=A_DIGEST, answer_text=f"s{index}-{image}"
            )
    return library


async def test_it_draws_only_active_categories(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§8: «из активных». Kills on: dropping the filter, which puts a theme
    the operator switched off back on the board — the one thing §5.3's soft
    delete exists to prevent."""
    library = await stock(sessions, ordinary=3)
    listed = await library.list_categories()
    await library.set_category_active(listed[0].id, is_active=False)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        drawn = await DatabaseCategoryBank(sessions).draw_categories(
            tx, 2, exclude=frozenset()
        )

    assert listed[0].id not in set(drawn)
    assert len(drawn) == 2


async def test_it_draws_only_ordinary_categories_not_secrets(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§8: a secret is «та же сущность с пометкой `is_secret`», and §3.4
    places each player's own secret from the roster.

    Kills on: dropping the `is_secret` filter — an ordinary cell is dealt
    with `revealed=True` (§3.4), so somebody's secret would appear on the
    board with its name showing."""
    library = await stock(sessions, ordinary=2, secrets=3)
    secrets = {row.id for row in await library.list_categories() if row.is_secret}

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        drawn = await DatabaseCategoryBank(sessions).draw_categories(
            tx, 2, exclude=frozenset()
        )

    assert not (set(drawn) & secrets)


async def test_it_never_draws_the_same_category_twice(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§8: «без повторов». Kills on: sampling with replacement, which
    breaks §2.8's bijection between groups and unplayed categories."""
    await stock(sessions, ordinary=12)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        drawn = await DatabaseCategoryBank(sessions).draw_categories(
            tx, 12, exclude=frozenset()
        )

    assert len(set(drawn)) == 12


async def test_it_honours_the_exclusion_set(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The exclusion is this match's secrets (see `Materialiser._deal`).

    Kills on: ignoring it — a player's own secret would land on the board
    twice, once hidden as theirs and once revealed as an ordinary cell."""
    library = await stock(sessions, ordinary=3)
    listed = await library.list_categories()
    excluded = CategoryId(listed[0].id)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        drawn = await DatabaseCategoryBank(sessions).draw_categories(
            tx, 2, exclude=frozenset({excluded})
        )
    assert excluded not in drawn

    # Deterministic, unlike the assertion above: with three categories, one
    # excluded and three asked for, honouring the exclusion leaves two and
    # must refuse. Ignoring it would succeed every time — where the check
    # above would pass by luck whenever the random draw happened to miss
    # the excluded row.
    with pytest.raises(ContentExhausted):
        async with uow.begin() as tx:
            await DatabaseCategoryBank(sessions).draw_categories(
                tx, 3, exclude=frozenset({excluded})
            )


async def test_it_raises_content_exhausted_when_the_library_is_too_small(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§8, and §6.3 routes it as an ordinary rejection rather than a
    quarantine.

    Kills on: returning a short tuple — `_deal` would zip it against the
    cells and produce a board with fewer categories than cells, which is
    not a board §2.8's invariants survive."""
    await stock(sessions, ordinary=2)
    uow = UnitOfWork(sessions)
    with pytest.raises(ContentExhausted):
        async with uow.begin() as tx:
            await DatabaseCategoryBank(sessions).draw_categories(tx, 5, exclude=frozenset())


async def test_asking_for_nothing_draws_nothing_and_touches_nothing(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A two-player board of two cells asks for zero ordinary categories.

    Kills on: issuing `LIMIT 0 FOR SHARE`, which locks nothing but costs a
    round trip inside the commit transaction — and on raising, which would
    make such a board impossible to deal."""
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        assert await DatabaseCategoryBank(sessions).draw_categories(
            tx, 0, exclude=frozenset()
        ) == ()


async def test_it_takes_for_share_on_every_category_it_draws(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§5.3: «отбор категорий на партию берёт `FOR SHARE` на родительскую
    строку».

    Asserted against PostgreSQL itself, because a lock is not observable
    from inside the transaction that holds it: a second transaction taking
    `FOR UPDATE` on the drawn rows must block while the drawing
    transaction is still open.

    Kills on: dropping `.with_for_update(read=True)` — a concurrent edit
    could then bump a category's version, and change its images, between
    selection and the commit that writes the deal."""
    await stock(sessions, ordinary=1)
    uow = UnitOfWork(sessions)
    # Two barriers, and both are load-bearing. `held` is the one this test
    # cannot do without: `create_task` only schedules, so without it the
    # contender can take its exclusive lock *first*, finish immediately,
    # and leave nothing for the poller below to ever see blocked — a
    # failure that looks like "FOR SHARE is missing" and is not.
    held = asyncio.Event()
    released = asyncio.Event()

    async def hold_the_lock() -> None:
        async with uow.begin() as tx:
            await DatabaseCategoryBank(sessions).draw_categories(tx, 1, exclude=frozenset())
            # The draw has returned, so the lock is taken and the
            # transaction is still open.
            held.set()
            await released.wait()

    holder = asyncio.create_task(hold_the_lock())
    await asyncio.wait_for(held.wait(), timeout=5)

    async def take_exclusive() -> None:
        async with sessions() as session, session.begin():
            await session.execute(text("SELECT id FROM categories FOR UPDATE"))

    contender = asyncio.create_task(take_exclusive())
    try:
        await wait_until_a_backend_is_blocked_on(sessions, "categories")
        assert not contender.done()
    finally:
        released.set()
        await holder
        await asyncio.wait_for(contender, timeout=5)


async def test_a_short_pack_is_drawn_whole_rather_than_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling 1: a category with forty pictures is playable, and §8 puts
    the error at the pack running out *in the duel*.

    Kills on: raising when fewer than `count` exist, which would make every
    category below `IMAGE_PACK_SIZE` unusable and quietly shrink the
    library to the categories nobody has finished stocking."""
    library = await stock(sessions, ordinary=1, images_each=4)
    category = CategoryId((await library.list_categories())[0].id)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        pack = await DatabaseCategoryBank(sessions).draw_images(tx, category, 60)

    assert len(pack) == 4


async def test_a_category_with_no_active_images_is_content_exhausted(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A category that cannot be played under any rule. §8 makes this the
    operator's explicit error, and §6.3 an ordinary rejection.

    Kills on: returning an empty tuple — `_declare_attack` does reject an
    empty `image_order`, but with `IMAGES_EXHAUSTED`, which reads to the
    operator as "this duel ran out" rather than "this category is empty"."""
    library = await stock(sessions, ordinary=1, images_each=1)
    detail = await library.category_detail((await library.list_categories())[0].id)
    await library.set_image_active(detail.images[0].id, is_active=False)

    uow = UnitOfWork(sessions)
    with pytest.raises(ContentExhausted):
        async with uow.begin() as tx:
            await DatabaseCategoryBank(sessions).draw_images(
                tx, CategoryId(detail.category.id), 60
            )


async def test_the_pack_contains_no_inactive_image(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    library = await stock(sessions, ordinary=1, images_each=5)
    detail = await library.category_detail((await library.list_categories())[0].id)
    await library.set_image_active(detail.images[0].id, is_active=False)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        pack = await DatabaseCategoryBank(sessions).draw_images(
            tx, CategoryId(detail.category.id), 60
        )

    assert detail.images[0].id not in set(pack)
    assert len(pack) == 4


async def test_the_pack_order_is_not_the_position_order(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling 2.

    Kills on: `ORDER BY position` — a category played twice in one evening
    would open both duels with the picture the room already saw, which is
    exactly what §3.5 draws the order to avoid.

    Twenty images and ten draws: the chance that a genuinely random draw
    matches `position` order every time is 1 in 20!^10, so a failure here
    means the order is fixed, not unlucky."""
    library = await stock(sessions, ordinary=1, images_each=20)
    detail = await library.category_detail((await library.list_categories())[0].id)
    by_position = tuple(image.id for image in detail.images)
    category = CategoryId(detail.category.id)

    uow = UnitOfWork(sessions)
    orders = set()
    for _ in range(10):
        async with uow.begin() as tx:
            orders.add(await DatabaseCategoryBank(sessions).draw_images(tx, category, 20))

    assert len(orders) > 1, "ten draws produced one order: the pack is not shuffled"
    assert orders != {by_position}


async def test_a_category_that_does_not_exist_is_content_exhausted(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: returning an empty tuple for an unknown id, which would
    reach the operator as "this duel ran out of pictures" for a category
    that was never in the library."""
    uow = UnitOfWork(sessions)
    with pytest.raises(ContentExhausted):
        async with uow.begin() as tx:
            await DatabaseCategoryBank(sessions).draw_images(
                tx, CategoryId(uuid4()), 60
            )


async def test_the_draw_is_visible_to_the_caller_s_own_transaction(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The bank runs on the caller's session, not on one of its own — which
    is what makes §5.3's locks live until the caller commits (§6.3).

    Kills on: opening a private session, which would take the locks in a
    transaction that ends the moment the draw returns."""
    await stock(sessions, ordinary=1)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await DatabaseCategoryBank(sessions).draw_categories(tx, 1, exclude=frozenset())
        # A row written in this transaction and read back through the same
        # session proves the bank is sharing it.
        seen = (await tx.session.execute(select(Category.id))).scalars().all()
    assert len(seen) == 1
