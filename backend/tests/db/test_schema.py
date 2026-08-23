"""What the schema itself guarantees, asserted against a live PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Category, Image, Match, MatchEventRow, MatchPlayer
from podvinsya.domain.state import MatchStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _a_match(sessions: async_sessionmaker[AsyncSession], **overrides: object) -> Match:
    match = Match(id=uuid4(), status=MatchStatus.SETUP.value, last_seq=1)
    for key, value in overrides.items():
        setattr(match, key, value)
    async with sessions() as session, session.begin():
        session.add(match)
    return match


async def test_two_events_cannot_share_a_seq_within_one_match(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match = await _a_match(sessions)
    async with sessions() as session, session.begin():
        session.add(
            MatchEventRow(
                match_id=match.id, seq=1, operation_id="op-1",
                type="match.created", schema_version=1, payload={},
            )
        )
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                MatchEventRow(
                    match_id=match.id, seq=1, operation_id="op-2",
                    type="match.started", schema_version=1, payload={},
                )
            )


async def test_one_operation_id_may_cover_several_events(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A JudgePass emits up to four events under one operation_id. A unique
    constraint here would reject the batch's own second row."""
    match = await _a_match(sessions)
    async with sessions() as session, session.begin():
        for seq, wire in enumerate(
            ("duel.pass_used", "duel.resolved", "match.player_eliminated", "match.won"), start=1
        ):
            session.add(
                MatchEventRow(
                    match_id=match.id, seq=seq, operation_id="op-judge",
                    type=wire, schema_version=1, payload={},
                )
            )
    async with sessions() as session:
        rows = (
            await session.execute(
                select(MatchEventRow.seq).where(MatchEventRow.operation_id == "op-judge")
            )
        ).scalars().all()
    assert sorted(rows) == [1, 2, 3, 4]


async def test_an_event_cannot_name_a_match_that_does_not_exist(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                MatchEventRow(
                    match_id=uuid4(), seq=1, operation_id="op-1",
                    type="match.created", schema_version=1, payload={},
                )
            )


async def test_the_status_check_admits_exactly_the_domain_statuses(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Derived from MatchStatus, not from a list retyped here: a status added
    to the domain and forgotten in the migration fails this test.

    This is the only thing standing between a new `MatchStatus` member and a
    stale migration: `alembic check` compares check constraints by name, not
    by body, so a `status IN (...)` text that falls out of sync with
    `MatchStatus` passes `alembic check` silently (see
    `test_models_and_migrations_do_not_disagree` in `test_migrations.py`).
    This test inserts every current domain status plus one that is not one,
    against the live database, so it fails the moment the migration's
    hardcoded list and `MatchStatus` disagree in either direction.
    """
    for status in MatchStatus:
        await _a_match(sessions, id=uuid4(), status=status.value)
    with pytest.raises(DBAPIError):
        await _a_match(sessions, id=uuid4(), status="lobby")


async def test_a_player_row_belongs_to_exactly_one_match(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match = await _a_match(sessions)
    player_id = uuid4()
    async with sessions() as session, session.begin():
        session.add(
            MatchPlayer(
                match_id=match.id, player_id=player_id,
                name="Рей", colour="#e5484d", eliminated=False,
            )
        )
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                MatchPlayer(
                    match_id=match.id, player_id=player_id,
                    name="Рей", colour="#3b82f6", eliminated=False,
                )
            )


async def test_deleting_a_match_takes_its_log_and_players_with_it(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Only the admin ever deletes a match, and a log row orphaned from its
    match is unreadable — there is no genesis event to fold it onto."""
    match = await _a_match(sessions)
    async with sessions() as session, session.begin():
        session.add(
            MatchEventRow(
                match_id=match.id, seq=1, operation_id="op-1",
                type="match.created", schema_version=1, payload={},
            )
        )
        session.add(
            MatchPlayer(
                match_id=match.id, player_id=uuid4(),
                name="Рей", colour="#e5484d", eliminated=False,
            )
        )
    async with sessions() as session, session.begin():
        await session.execute(text("DELETE FROM matches WHERE id = :id"), {"id": match.id})
    async with sessions() as session:
        left_events = (await session.execute(select(MatchEventRow.seq))).scalars().all()
        left_players = (await session.execute(select(MatchPlayer.player_id))).scalars().all()
    assert left_events == []
    assert left_players == []


async def _a_category(
    sessions: async_sessionmaker[AsyncSession], **overrides: object
) -> Category:
    category = Category(id=uuid4(), title="История", is_secret=False, is_active=True, version=1)
    for key, value in overrides.items():
        setattr(category, key, value)
    async with sessions() as session, session.begin():
        session.add(category)
    return category


A_DIGEST = "a" * 64


async def test_an_image_digest_must_be_a_sha256(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: dropping the check constraint. The media plan writes here
    too, and a row whose digest is not one points at an object that cannot
    exist under any content-addressed scheme (§7.6)."""
    category = await _a_category(sessions)
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                Image(
                    id=uuid4(),
                    category_id=category.id,
                    media_sha256="not-a-digest",
                    answer_text="Гагарин",
                    position=0,
                )
            )


async def test_an_uppercase_digest_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Content addressing only works if one object has one name. Kills on:
    a case-insensitive pattern, which would let the same bytes be stored
    under two digests that differ only in case."""
    category = await _a_category(sessions)
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                Image(
                    id=uuid4(),
                    category_id=category.id,
                    media_sha256="A" * 64,
                    answer_text="Гагарин",
                    position=0,
                )
            )


async def test_a_negative_position_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    category = await _a_category(sessions)
    with pytest.raises(IntegrityError):
        async with sessions() as session, session.begin():
            session.add(
                Image(
                    id=uuid4(),
                    category_id=category.id,
                    media_sha256=A_DIGEST,
                    answer_text="Гагарин",
                    position=-1,
                )
            )


async def test_a_category_starts_active_at_version_one(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: defaulting `version` to 0, which makes the first bump
    produce 1 and be indistinguishable from a category never edited."""
    async with sessions() as session, session.begin():
        category = Category(id=uuid4(), title="Кино")
        session.add(category)
    async with sessions() as session:
        stored = (
            await session.execute(select(Category).where(Category.id == category.id))
        ).scalar_one()
    assert (stored.version, stored.is_active, stored.is_secret) == (1, True, False)


async def test_deleting_a_category_cascades_to_its_images(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Nothing in this system performs a hard delete (§5.3: «контент
    удаляется только мягко»). Asserted so that if a later plan adds an
    administrative purge, it cannot leave orphaned images behind.

    Kills on: dropping `ondelete="CASCADE"`, which turns that future purge
    into a foreign-key violation or, worse, orphan rows."""
    category = await _a_category(sessions)
    async with sessions() as session, session.begin():
        session.add(
            Image(
                id=uuid4(),
                category_id=category.id,
                media_sha256=A_DIGEST,
                answer_text="Гагарин",
                position=0,
            )
        )

    async with sessions() as session, session.begin():
        await session.execute(text("DELETE FROM categories WHERE id = :id"), {"id": category.id})

    async with sessions() as session:
        remaining = (await session.execute(select(Image))).scalars().all()
    assert remaining == []
