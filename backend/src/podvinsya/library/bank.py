"""Selection: plan 3's `CategoryBank`, over the real library.

Two rules from §8 and one from §5.3 shape everything here.

§8: «Отбор на партию: из активных, без повторов, число равно числу клеток
поля.» So `draw_categories` filters on `is_active`, excludes what the
caller names, and refuses rather than returning a short tuple — a board
with fewer categories than cells is not a board §2.8's invariants survive.

§8 again: «исчерпание категории посреди дуэли трактуется как дефект
контента, а не игровая ситуация.» So `draw_images` returns *up to* `count`
(ruling 1). A category with forty pictures is playable; the error §8 names
is the pack running out **in the duel**, which the domain already raises as
`IMAGES_EXHAUSTED`. `ContentExhausted` here is reserved for a category with
no active images at all — one that cannot be played under any rule.

§5.3: «отбор категорий на партию берёт `FOR SHARE` на родительскую строку.»
Both methods run on the caller's transaction, which is what makes those
locks live until the commit — and why §6.3 replays a retried attempt whole
rather than reusing a pack drawn under locks that are already gone.
"""

from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Category, Image
from podvinsya.db.store import TransactionContext
from podvinsya.domain.ids import CategoryId, ImageId
from podvinsya.services.ports import ContentExhausted, Transaction


def _session_of(tx: Transaction) -> AsyncSession:
    """The caller's own session, so the `FOR SHARE` locks below belong to
    the caller's transaction and are held until it commits (§5.3, §6.3).

    `Transaction` is a Protocol with one method; the concrete type plan 2
    supplies is `TransactionContext`, which carries the session. Narrowing
    here rather than widening the port keeps `services/ports.py` free of
    SQLAlchemy.
    """
    return cast(TransactionContext, tx).session


class DatabaseCategoryBank:
    """`services.ports.CategoryBank`, satisfied structurally.

    `sessions` is held for nothing today — every method uses the caller's
    transaction — and is taken anyway so the constructor matches every
    other library component and a future read that legitimately stands
    outside a transaction has somewhere to go.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def draw_categories(
        self, tx: Transaction, count: int, *, exclude: frozenset[CategoryId]
    ) -> tuple[CategoryId, ...]:
        """§8's selection, under §5.3's lock.

        Secrets are excluded structurally, not by the caller: §8 makes a
        secret «та же сущность с пометкой `is_secret`», and §3.4 places each
        player's own secret from the roster. An ordinary cell drawn from the
        secret pool would be dealt with `revealed=True` — somebody's secret,
        on the board, named.
        """
        if count <= 0:
            return ()
        session = _session_of(tx)
        statement = (
            select(Category.id)
            .where(
                Category.is_active.is_(True),
                Category.is_secret.is_(False),
                Category.id.notin_(list(exclude)) if exclude else Category.id.isnot(None),
            )
            .order_by(func.random())
            .limit(count)
            # SQLAlchemy spells §5.3's `FOR SHARE` this way: `read=True`
            # is a shared lock, not an exclusive one. Two selections may
            # hold it at once — what it blocks is an edit to the row,
            # which is exactly the invariant §5.3 describes.
            .with_for_update(read=True)
        )
        drawn = tuple((await session.execute(statement)).scalars().all())
        if len(drawn) < count:
            raise ContentExhausted(
                f"asked for {count} categories, the active library has {len(drawn)}"
            )
        return tuple(CategoryId(category_id) for category_id in drawn)

    async def draw_images(
        self, tx: Transaction, category: CategoryId, count: int
    ) -> tuple[ImageId, ...]:
        """The whole pack, drawn once at declaration (§3.5).

        Random order, not `position` order (ruling 2): `position` is the
        operator's ordering for the admin screen, and a category played
        twice in one evening would otherwise open both duels with the same
        picture — visible to the room, and the reason §3.5 draws the order
        rather than fixing it.

        Up to `count`, never fewer than one. See the module docstring.
        """
        session = _session_of(tx)
        statement = (
            select(Image.id)
            .where(Image.category_id == category, Image.is_active.is_(True))
            .order_by(func.random())
            .limit(count)
        )
        drawn = tuple((await session.execute(statement)).scalars().all())
        if not drawn:
            raise ContentExhausted(f"category {category} has no active images")
        return tuple(ImageId(image_id) for image_id in drawn)
