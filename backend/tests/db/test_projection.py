"""The read model is not authoritative, and the test that matters is that it
says the same thing whether it was maintained event by event or rebuilt from
the log in one pass. Anything the incremental path can do that the rebuild
cannot is a bug in the incremental path."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.db.models import Match, MatchPlayer
from budge.db.projection import rebuild
from budge.db.repository import MatchRepository
from budge.db.store import UnitOfWork
from budge.domain.events import MatchCreated, MatchReset, MatchStarted, MatchWon
from budge.domain.evolve import fold
from budge.domain.genesis import create_initial_state
from budge.domain.ids import MatchId
from budge.domain.state import MatchStatus
from support.streams import Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _play_whole_match(
    sessions: async_sessionmaker[AsyncSession], *, until: type | None = None
) -> Recorded:
    """Write the stream to the database in full -- or up to and including the
    first event of type `until`.

    The boundary is needed now that the stream ends in resets (§A):
    "the projection follows the match to its end" is a claim about
    `MatchWon`, and a snapshot taken after a reset says nothing about it.
    The returned `Recorded` is re-folded to the same boundary, so its
    `state` describes what actually reached the database -- not the
    stream's final, post-reset state, which every field a truncated caller
    reads (`winner`, `status`, ...) would otherwise disagree with.
    """
    recorded = build_rich_stream()
    events = list(recorded.events)
    if until is not None:
        cut = next(i for i, event in enumerate(events) if isinstance(event, until))
        events = events[: cut + 1]
        recorded = Recorded(
            state=fold(
                create_initial_state(
                    recorded.state.id, recorded.state.board, recorded.state.settings
                ),
                events,
            ),
            events=tuple(events),
        )
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1:]):
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
    recorded = await _play_whole_match(sessions, until=MatchWon)
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
    `match_players` corruption a repair has to fix.

    Truncated to `MatchWon` (§A): the full stream's trailing resets empty
    the roster, and this test needs a real player row to rename.
    """
    recorded = await _play_whole_match(sessions, until=MatchWon)
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


async def test_a_full_reset_empties_the_roster_and_returns_the_match_to_setup(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§A.6: a reset in the read model is the first half of `rebuild`.

    Kills on: a reset not reflected in the projection -- the match list
    would show a winner for a match that is once again sitting in setup.
    """
    recorded = await _play_whole_match(sessions)
    status, winner_id, players = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.SETUP.value
    assert winner_id is None
    assert players == []


async def test_a_reset_that_keeps_the_roster_un_eliminates_everyone(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The stream ends in two resets: first with the roster kept, then
    without it. This checks the first one -- the snapshot is taken before
    the second.

    Kills on: a `keep_roster=True` branch that forgets to clear
    `eliminated` -- the match would be replayed with a player already out.
    """
    recorded = build_rich_stream()
    events = list(recorded.events)
    first_reset = next(i for i, event in enumerate(events) if isinstance(event, MatchReset))
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1 : first_reset + 1]):
        async with uow.begin() as tx:
            await tx.append(
                recorded.state.id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )
    status, winner_id, players = await _snapshot(sessions, recorded.state.id)
    assert status == MatchStatus.SETUP.value
    assert winner_id is None
    assert len(players) == 2
    assert all(not eliminated for _, _, _, eliminated in players)
