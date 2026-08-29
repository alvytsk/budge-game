"""After a commit whose outcome is unknown, the runtime asks the log what
actually happened. Three answers, not two: a batch that never landed is a
retry, a batch that landed is progress, and anything else is a quarantine."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.db.repository import MatchRepository
from budge.db.store import Reconciliation, UnitOfWork
from budge.domain.events import Event, MatchCreated
from budge.domain.ids import MatchId
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _seeded(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[MatchId, tuple[Event, ...]]:
    recorded = build_rich_stream()
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(recorded.state.id, created, operation_id="op-create")
    return recorded.state.id, recorded.events[1:]


async def test_a_committed_batch_reconciles_as_matched(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    batch = rest[:3]
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=batch, operation_id="op-1")
    outcome = await uow.reconcile(match_id, "op-1", expected_last_seq=1, events=batch)
    assert outcome is Reconciliation.MATCHED


async def test_a_batch_that_never_landed_reconciles_as_absent(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Absent means retry, not quarantine — the distinction a boolean loses."""
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    outcome = await uow.reconcile(match_id, "op-ghost", expected_last_seq=1, events=rest[:3])
    assert outcome is Reconciliation.ABSENT


async def test_a_different_number_of_rows_diverges(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:3], operation_id="op-1")
    outcome = await uow.reconcile(match_id, "op-1", expected_last_seq=1, events=rest[:2])
    assert outcome is Reconciliation.DIVERGED


async def test_different_types_in_the_same_positions_diverge(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """"Almost matched" is not an outcome: the committed batch and the batch
    in memory must be the same batch.

    `rest[0]` and `rest[1]` are `PlayerAdded` (player 1) and `SecretAssigned`
    (player 1's secret) — genuinely different wire types, so swapping them
    is a real reordering, not a no-op that happens to compare equal.
    """
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:3], operation_id="op-1")
    reordered = (rest[1], rest[0], rest[2])
    outcome = await uow.reconcile(match_id, "op-1", expected_last_seq=1, events=reordered)
    assert outcome is Reconciliation.DIVERGED


async def test_a_batch_at_the_wrong_seq_range_diverges(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    match_id, rest = await _seeded(sessions)
    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=1, events=rest[:2], operation_id="op-1")
    async with uow.begin() as tx:
        await tx.append(match_id, expected_last_seq=3, events=rest[2:4], operation_id="op-2")
    outcome = await uow.reconcile(match_id, "op-2", expected_last_seq=1, events=rest[2:4])
    assert outcome is Reconciliation.DIVERGED
