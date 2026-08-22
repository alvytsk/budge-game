"""The append is the only way an event reaches the log, and the optimistic
guard is the only thing standing between two writers and a corrupted
stream."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.errors import ConcurrentModification
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import MatchId
from support.db import wait_until_a_backend_is_blocked_on
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _genesis(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[MatchId, tuple[Event, ...]]:
    """A created match plus the rest of a real stream, ready to append."""
    recorded = build_rich_stream()
    match_id = recorded.state.id
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    return match_id, recorded.events[1:]


async def test_create_writes_the_match_row_and_the_genesis_event(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, _ = await _genesis(sessions)
    async with sessions() as session:
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
        rows = (
            await session.execute(select(MatchEventRow).order_by(MatchEventRow.seq))
        ).scalars().all()
    assert match.last_seq == 1
    assert [(row.seq, row.type) for row in rows] == [(1, "match.created")]


async def test_a_batch_lands_contiguously_under_one_operation_id(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _genesis(sessions)
    batch = rest[:4]
    async with UnitOfWork(sessions).begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=batch, operation_id="op-batch")
    async with sessions() as session:
        rows = (
            await session.execute(
                select(MatchEventRow)
                .where(MatchEventRow.operation_id == "op-batch")
                .order_by(MatchEventRow.seq)
            )
        ).scalars().all()
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
    assert [row.seq for row in rows] == [2, 3, 4, 5]
    assert match.last_seq == 5


async def test_the_whole_stream_appends_one_command_at_a_time(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _genesis(sessions)
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(rest):
        async with uow.begin() as tx:
            await tx.append(
                match_id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    async with sessions() as session:
        seqs = (
            await session.execute(select(MatchEventRow.seq).order_by(MatchEventRow.seq))
        ).scalars().all()
    assert seqs == list(range(1, len(rest) + 2))


async def test_a_stale_expected_last_seq_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Someone else already advanced this match past what this caller's
    decide() saw. Appending anyway would write events decided against state
    that is no longer current."""
    match_id, rest = await _genesis(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:1], operation_id="op-a")
    with pytest.raises(ConcurrentModification):
        async with uow.begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[1:2], operation_id="op-b")
    async with sessions() as session:
        rows = (await session.execute(select(MatchEventRow.seq))).scalars().all()
    assert sorted(rows) == [1, 2], "the refused batch must leave no trace"


async def test_two_concurrent_appenders_cannot_both_win(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The UPDATE takes the row lock before any INSERT, so the second writer
    blocks in the database rather than racing to the same seq."""
    match_id, rest = await _genesis(sessions)
    uow = UnitOfWork(sessions)
    started = asyncio.Event()

    async def first() -> None:
        async with uow.begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[:1], operation_id="op-1")
            started.set()
            await wait_until_a_backend_is_blocked_on(sessions, "matches")

    async def second() -> None:
        await started.wait()
        async with uow.begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[1:2], operation_id="op-2")

    results = await asyncio.gather(first(), second(), return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrentModification)


async def test_an_empty_batch_is_a_programming_error(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.2 resolves a no-op without opening the log at all. Reaching append
    with nothing to write means the caller lost track of that."""
    match_id, _ = await _genesis(sessions)
    with pytest.raises(ValueError):
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=(), operation_id="op-empty")


async def test_a_failure_after_append_rolls_the_whole_batch_back(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _genesis(sessions)
    with pytest.raises(RuntimeError):
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest[:2], operation_id="op-x")
            raise RuntimeError("the materialiser blew up after the append")
    async with sessions() as session:
        rows = (await session.execute(select(MatchEventRow.seq))).scalars().all()
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
    assert rows == [1]
    assert match.last_seq == 1, "last_seq must roll back with the rows it counts"
