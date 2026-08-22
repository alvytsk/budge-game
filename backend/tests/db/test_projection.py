"""The read model is not authoritative, and the test that matters is that it
says the same thing whether it was maintained event by event or rebuilt from
the log in one pass. Anything the incremental path can do that the rebuild
cannot is a bug in the incremental path."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.models import Match, MatchPlayer
from podvinsya.db.projection import rebuild
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import MatchCreated, MatchStarted
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchStatus
from support.streams import Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _play_whole_match(sessions: async_sessionmaker[AsyncSession]) -> Recorded:
    recorded = build_rich_stream()
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(recorded.events[1:]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    return recorded


async def _snapshot(
    sessions: async_sessionmaker[AsyncSession], match_id: MatchId
) -> tuple[str, UUID | None, list[tuple[UUID, str, str, bool]]]:
    async with sessions() as session:
        match = (await session.execute(select(Match).where(Match.id == match_id))).scalar_one()
        players = (
            await session.execute(
                select(MatchPlayer)
                .where(MatchPlayer.match_id == match_id)
                .order_by(MatchPlayer.player_id)
            )
        ).scalars().all()
    return (
        match.status,
        match.winner_id,
        [(p.player_id, p.name, p.colour, p.eliminated) for p in players],
    )


async def test_the_projection_follows_the_match_to_its_end(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = await _play_whole_match(sessions)
    status, winner_id, players = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.FINISHED.value
    assert winner_id == recorded.state.winner
    assert len(players) == 2
    assert sum(1 for _, _, _, eliminated in players if eliminated) == 1
    assert {name for _, name, _, _ in players} == {"Игрок 1", "Игрок 2"}


async def test_the_projection_reflects_running_before_the_match_ends(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """`MatchWon` sets `status` to `finished` unconditionally, so a snapshot
    taken only at the end cannot tell the `MatchStarted` branch apart from a
    no-op: a match whose row sat at `setup` for its whole run still lands on
    the right terminal value. Stop the stream right after `MatchStarted`
    instead, and check the status in between."""
    recorded = build_rich_stream()
    started_index = next(
        index for index, event in enumerate(recorded.events) if isinstance(event, MatchStarted)
    )
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(recorded.events[1 : started_index + 1]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    status, _, _ = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.RUNNING.value


async def test_a_rebuild_reproduces_the_incremental_projection(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = await _play_whole_match(sessions)
    incremental = await _snapshot(sessions, recorded.state.id)
    async with sessions() as session, session.begin():
        await rebuild(session, recorded.state.id, recorded.events)
    assert await _snapshot(sessions, recorded.state.id) == incremental


async def test_a_rebuild_discards_whatever_was_there_before(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Not authoritative means exactly this: a corrupted read model is
    repaired by replaying the log, not by hand. Corrupting only the
    `matches` row would leave `rebuild`'s handling of `match_players`
    unproven, so this also renames an existing player's row and plants an
    extra player row that never existed in the log — the two shapes of
    `match_players` corruption a repair has to fix."""
    recorded = await _play_whole_match(sessions)
    expected = await _snapshot(sessions, recorded.state.id)
    async with sessions() as session, session.begin():
        match = (
            await session.execute(select(Match).where(Match.id == recorded.state.id))
        ).scalar_one()
        match.status = MatchStatus.SETUP.value
        match.winner_id = None
        existing_players = (
            await session.execute(
                select(MatchPlayer)
                .where(MatchPlayer.match_id == recorded.state.id)
                .order_by(MatchPlayer.player_id)
            )
        ).scalars().all()
        stale = existing_players[0]
        stale.name = "Stale Name"
        stale.colour = "#000000"
        stale.eliminated = not stale.eliminated
        session.add(
            MatchPlayer(
                match_id=recorded.state.id,
                player_id=uuid4(),
                name="Never Existed",
                colour="#ffffff",
                eliminated=False,
            )
        )
    async with sessions() as session, session.begin():
        await rebuild(session, recorded.state.id, recorded.events)
    assert await _snapshot(sessions, recorded.state.id) == expected


async def test_a_rebuild_from_the_database_alone_restores_the_projection(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The claim that the read model is not authoritative and is rebuilt
    from the log is only true if something can produce the events `rebuild`
    needs by reading the database — not by reusing the in-memory `Recorded`
    the test happened to build the match from. This test never touches
    `recorded.events`: it goes back through `MatchRepository.read_events`,
    the same path a real recovery would use."""
    recorded = await _play_whole_match(sessions)
    expected = await _snapshot(sessions, recorded.state.id)
    async with sessions() as session, session.begin():
        match = (
            await session.execute(select(Match).where(Match.id == recorded.state.id))
        ).scalar_one()
        match.status = MatchStatus.SETUP.value
        match.winner_id = None
        await session.execute(delete(MatchPlayer).where(MatchPlayer.match_id == recorded.state.id))
    events = await MatchRepository(sessions).read_events(recorded.state.id)
    async with sessions() as session, session.begin():
        await rebuild(session, recorded.state.id, events)
    assert await _snapshot(sessions, recorded.state.id) == expected


async def test_the_projection_is_written_in_the_appending_transaction(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A projection updated after the commit can disagree with the log
    across a crash. It goes in the same transaction or it is wrong."""
    recorded = build_rich_stream()
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    with pytest.raises(RuntimeError):
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=1,
                events=recorded.events[1:3],
                operation_id="op-1",
            )
            raise RuntimeError("crash between the append and the commit")
    async with sessions() as session:
        players = (await session.execute(select(MatchPlayer))).scalars().all()
    assert players == []
