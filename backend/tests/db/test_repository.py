"""Recovery is «just fold the log» or it is nothing. These tests hold the
loaded state against the state the domain produced in memory — not against a
hand-written expectation, which could agree with a bug in both."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.errors import EventStreamCorrupt, MatchNotFound
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import DuelPhase
from support.streams import Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _persist(
    sessions: async_sessionmaker[AsyncSession],
    upto: int | None = None,
    *,
    recorded: Recorded | None = None,
) -> tuple[MatchId, tuple[Event, ...]]:
    """Persist a real stream, optionally truncated to catch a match mid-duel.

    `build_rich_stream` mints fresh random ids on every call — nothing here
    monkeypatches its `uuid4` for determinism — so a caller that needs to
    compare the loaded state against the exact state that produced it must
    pass that same `Recorded` in rather than let this helper build its own;
    two independent calls to `build_rich_stream` are never equal.
    """
    recorded = recorded if recorded is not None else build_rich_stream()
    events = recorded.events if upto is None else recorded.events[:upto]
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
    return recorded.state.id, events


async def test_a_finished_match_loads_back_exactly(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    match_id, _ = await _persist(sessions, recorded=recorded)
    loaded = await MatchRepository(sessions).load(match_id)
    assert loaded.state == recorded.state, (
        "the fold of the persisted log must equal the fold that produced it"
    )
    assert loaded.last_seq == len(recorded.events)


async def test_a_match_caught_mid_duel_loads_with_its_duel_intact(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§11: «свёртка лога после падения посреди дуэли даёт … нетронутые
    остатки». The pause of §4.4 is the runtime's to emit; the log says what
    the log says."""
    recorded = build_rich_stream()
    cut = next(
        index
        for index, event in enumerate(recorded.events)
        if type(event).__name__ == "DuelResumed"
    ) + 1
    match_id, events = await _persist(sessions, upto=cut)
    loaded = await MatchRepository(sessions).load(match_id)
    duel = loaded.state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.RUNNING
    assert duel.anchor is not None, "the log recorded a running clock; loading must not pause it"
    assert loaded.last_seq == len(events)


async def test_the_loaded_seq_agrees_with_the_stored_last_seq(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Two counters of the same thing: the state's own seq, and the column
    the optimistic append guards on. They must never drift."""
    match_id, events = await _persist(sessions)
    loaded = await MatchRepository(sessions).load(match_id)
    async with sessions() as session:
        stored = (
            await session.execute(
                text("SELECT last_seq FROM matches WHERE id = :id"), {"id": match_id}
            )
        ).scalar_one()
    assert loaded.state.seq == loaded.last_seq == stored


async def test_an_unknown_match_is_not_an_empty_match(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(MatchNotFound):
        await MatchRepository(sessions).load(MatchId(uuid4()))


async def test_a_log_that_does_not_begin_with_genesis_is_corrupt(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Without MatchCreated there is no board and no settings to fold onto,
    so there is nothing to recover — and guessing would be worse.

    Seq 1 is rewritten rather than deleted: deleting it leaves a hole, and
    the contiguity check would then raise first, so this test would pass
    with the genesis check removed.
    """
    match_id, _ = await _persist(sessions, upto=4)
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE match_events SET type = 'duel.started', "
                "payload = '{\"anchor\": \"2026-08-22T12:00:00Z\"}'::jsonb "
                "WHERE match_id = :id AND seq = 1"
            ),
            {"id": match_id},
        )
    with pytest.raises(EventStreamCorrupt) as excinfo:
        await MatchRepository(sessions).load(match_id)
    # The message always contains the literal "not MatchCreated", so
    # asserting on that alone would hold even if the interpolated offending
    # type name were wrong or missing. Assert on the offending type itself —
    # the row above was planted as duel.started — to prove the
    # interpolation actually works.
    assert "DuelStarted" in str(excinfo.value)


async def test_a_row_with_a_malformed_payload_is_corrupt_not_a_pydantic_error(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The codec's own unit test proves `decode` translates a Pydantic
    `ValidationError`; this proves it end to end through `load` against a
    real row. A missing field, a wrong type, or a payload shaped for a
    different wire type is the likeliest real corruption — what a forgotten
    upcaster looks like — and it must surface as `EventStreamCorrupt`, the
    type a runtime's `except EventStreamCorrupt:` would actually catch, not
    as a bare `pydantic.ValidationError` sailing straight past it.
    """
    match_id, _ = await _persist(sessions, upto=4)
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE match_events SET payload = '{}'::jsonb "
                "WHERE match_id = :id AND seq = 2"
            ),
            {"id": match_id},
        )
    with pytest.raises(EventStreamCorrupt) as excinfo:
        await MatchRepository(sessions).load(match_id)
    assert "match.player_added" in str(excinfo.value)


async def test_a_gap_in_the_log_is_corrupt(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, _ = await _persist(sessions, upto=5)
    async with sessions() as session, session.begin():
        await session.execute(
            text("DELETE FROM match_events WHERE match_id = :id AND seq = 3"), {"id": match_id}
        )
    with pytest.raises(EventStreamCorrupt):
        await MatchRepository(sessions).load(match_id)
