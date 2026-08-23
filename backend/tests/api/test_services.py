"""`MatchLifecycle` and `CommandGateway` against a real log and a real
runtime.

Nothing here fakes the manager. Ruling 5 makes this the single door every
command goes through, and a gateway tested against a stub would prove only
that the stub was called.
"""

import asyncio
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.api.hub import MatchHub
from budge.api.services import CommandGateway, MatchLifecycle
from budge.db.models import Match, MatchEventRow
from budge.db.repository import MatchRepository
from budge.db.store import UnitOfWork
from budge.domain.actions import AddPlayer, AssignSecret, DealBoard
from budge.domain.board import BoardSize
from budge.domain.errors import RejectionReason
from budge.domain.ids import CategoryId, MatchId, PlayerId
from budge.domain.settings import MatchSettings
from budge.domain.state import MatchStatus
from budge.runtime.manager import MatchManager
from budge.runtime.materialiser import Materialiser
from budge.runtime.origins import Accepted, Failed, Rejected
from budge.services.ports import RuntimeCode
from support.fakes import FakeCategoryBank, FakeClock
from support.streams import BASE_TIME

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BOARD = BoardSize(3, 4)


def build(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bank: FakeCategoryBank | None = None,
) -> tuple[MatchLifecycle, CommandGateway, MatchManager]:
    clock = FakeClock(BASE_TIME)
    repository = MatchRepository(sessions)
    manager = MatchManager(
        repository,
        UnitOfWork(sessions),
        lambda: Materialiser(clock, repository, bank or FakeCategoryBank(), Random(0)),
        MatchHub(capacity=8),
        clock,
    )
    lifecycle = MatchLifecycle(repository, manager, clock, sessions)
    return lifecycle, CommandGateway(lifecycle, manager), manager


async def test_creating_a_match_writes_genesis_and_nothing_else(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    lifecycle, _gateway, manager = build(sessions)
    try:
        match_id, outcome = await lifecycle.create(BOARD, MatchSettings(), 2)
    finally:
        await manager.shutdown()

    assert isinstance(outcome, Accepted)
    async with sessions() as session:
        rows = (
            (await session.execute(select(MatchEventRow).where(MatchEventRow.match_id == match_id)))
            .scalars()
            .all()
        )
        match_row = (
            await session.execute(select(Match).where(Match.id == match_id))
        ).scalar_one()
    assert [row.seq for row in rows] == [1]
    assert rows[0].type == "match.created"
    assert match_row.last_seq == 1
    assert match_row.status == MatchStatus.SETUP.value


async def test_an_invalid_board_is_rejected_before_anything_is_written(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling 4: `decide` owns validity, and a refused creation must leave
    no row and no event behind.

    Kills on: writing genesis and then deciding — an invalid board would
    round-trip into a `matches` row that no valid log ever explains."""
    lifecycle, _gateway, manager = build(sessions)
    try:
        match_id, outcome = await lifecycle.create(BoardSize(2, 2), MatchSettings(), 2)
    finally:
        await manager.shutdown()

    assert isinstance(outcome, Rejected)
    assert outcome.reason is RejectionReason.BOARD_INVALID
    async with sessions() as session:
        assert (
            await session.execute(select(Match).where(Match.id == match_id))
        ).scalar_one_or_none() is None


async def test_the_gateway_starts_a_match_that_is_not_yet_live(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """First touch starts the runtime. Kills on: dropping `ensure_live`,
    which leaves `MatchManager.submit` raising `KeyError` for every command
    sent to a match this process has not started."""
    lifecycle, gateway, manager = build(sessions)
    try:
        match_id, _ = await lifecycle.create(BOARD, MatchSettings(), 2)
        assert manager.runtime_for(match_id) is None

        outcome = await gateway.submit(
            match_id, AddPlayer(player_id=PlayerId(uuid4()), name="A", colour="#111111")
        )

        assert isinstance(outcome, Accepted)
        assert manager.runtime_for(match_id) is not None
    finally:
        await manager.shutdown()


async def test_two_concurrent_first_touches_start_one_runtime(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The console and the stage screen open at the same moment — which is
    how every show begins.

    Kills on: letting `MatchAlreadyRunning` escape `ensure_live`, which
    turns the second of two simultaneous sockets into a 500."""
    lifecycle, gateway, manager = build(sessions)
    try:
        match_id, _ = await lifecycle.create(BOARD, MatchSettings(), 2)

        first, second = await asyncio.gather(
            gateway.submit(
                match_id, AddPlayer(player_id=PlayerId(uuid4()), name="A", colour="#111111")
            ),
            gateway.submit(
                match_id, AddPlayer(player_id=PlayerId(uuid4()), name="B", colour="#222222")
            ),
        )

        assert isinstance(first, Accepted)
        assert isinstance(second, Accepted)
        runtime = manager.runtime_for(match_id)
        assert runtime is not None
        assert len(runtime.state.players) == 2
    finally:
        await manager.shutdown()


async def test_a_rejection_comes_back_verbatim(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: the gateway translating an outcome. Ruling 4 puts the
    domain's own reason on the wire, and a gateway that summarised would
    make `outcomes.py`'s mapping unreachable."""
    lifecycle, gateway, manager = build(sessions)
    try:
        match_id, _ = await lifecycle.create(BOARD, MatchSettings(), 2)
        player = PlayerId(uuid4())
        await gateway.submit(match_id, AddPlayer(player_id=player, name="A", colour="#111111"))

        outcome = await gateway.submit(
            match_id, AddPlayer(player_id=player, name="A again", colour="#111111")
        )

        assert isinstance(outcome, Rejected)
        assert outcome.reason is RejectionReason.DUPLICATE_PLAYER
    finally:
        await manager.shutdown()


async def test_dealing_without_a_library_is_a_refusal_and_not_a_quarantine(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.3 and §8: a content shortfall is «обычный отказ, не авария».

    Kills on: wiring a bank that raises something §6.3 quarantines on,
    which takes a match off the air for a gap §8 calls an administrator's
    problem — and leaves the operator unable to press "перераздать"."""
    lifecycle, gateway, manager = build(sessions, bank=FakeCategoryBank(exhaust_after=0))
    try:
        match_id, _ = await lifecycle.create(BOARD, MatchSettings(), 2)
        for index in range(2):
            player = PlayerId(uuid4())
            await gateway.submit(
                match_id, AddPlayer(player_id=player, name=f"P{index}", colour="#111111")
            )
            # §3.4 deals each player's secret onto one of their own cells,
            # so a deal is illegal until every player has one — reaching
            # the bank at all requires getting past that guard first.
            await gateway.submit(
                match_id, AssignSecret(player_id=player, category=CategoryId(uuid4()))
            )

        outcome = await gateway.submit(match_id, DealBoard())

        assert isinstance(outcome, Failed)
        assert outcome.code is RuntimeCode.CONTENT_UNAVAILABLE
        runtime = manager.runtime_for(match_id)
        assert runtime is not None
        assert not runtime.quarantined
    finally:
        await manager.shutdown()


async def test_the_state_of_a_match_is_the_runtime_s_own(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: folding a separate snapshot, which could disagree with the
    copy every command is decided against while one is in flight."""
    lifecycle, gateway, manager = build(sessions)
    try:
        match_id, _ = await lifecycle.create(BOARD, MatchSettings(), 2)
        await gateway.submit(
            match_id, AddPlayer(player_id=PlayerId(uuid4()), name="A", colour="#111111")
        )

        state = await lifecycle.state_of(match_id)
        runtime = manager.runtime_for(match_id)

        assert runtime is not None
        assert state is runtime.state
    finally:
        await manager.shutdown()


async def test_a_snapshot_recovers_a_match_this_process_never_started(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§4.4's restart, from the API's side: a console reconnecting after a
    restart asks for a snapshot, and gets one.

    Kills on: `state_of` reading only the registry, which would answer
    every post-restart request with "unknown match"."""
    first_lifecycle, first_gateway, first_manager = build(sessions)
    try:
        match_id, _ = await first_lifecycle.create(BOARD, MatchSettings(), 2)
        await first_gateway.submit(
            match_id, AddPlayer(player_id=PlayerId(uuid4()), name="A", colour="#111111")
        )
    finally:
        await first_manager.shutdown()

    second_lifecycle, _gateway, second_manager = build(sessions)
    try:
        state = await second_lifecycle.state_of(match_id)
    finally:
        await second_manager.shutdown()

    assert len(state.players) == 1
    assert state.seq == 2


async def test_the_list_reads_the_read_model(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Ruling 14: §5.2's projection tables, finally read by something.

    Kills on: folding every log to render the admin list, which would make
    the read model dead code with tests — and would read every event of
    every match ever played to draw one screen."""
    lifecycle, gateway, manager = build(sessions)
    try:
        match_id, _ = await lifecycle.create(BOARD, MatchSettings(), 2)
        await gateway.submit(
            match_id, AddPlayer(player_id=PlayerId(uuid4()), name="Аня", colour="#111111")
        )

        summaries = await lifecycle.summaries()
    finally:
        await manager.shutdown()

    assert [summary.id for summary in summaries] == [match_id]
    assert summaries[0].status == MatchStatus.SETUP.value
    assert summaries[0].players == (("Аня", "#111111", False),)


async def test_the_list_is_empty_when_nothing_has_been_created(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    lifecycle, _gateway, manager = build(sessions)
    try:
        assert await lifecycle.summaries() == ()
    finally:
        await manager.shutdown()


async def test_a_match_id_nobody_created_cannot_be_started(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Kills on: `ensure_live` inventing a match. Recovery folds a log, and
    a match with no log has nothing to fold — the caller must hear that,
    not receive an empty match."""
    from budge.db.errors import MatchNotFound

    lifecycle, _gateway, manager = build(sessions)
    try:
        with pytest.raises(MatchNotFound):
            await lifecycle.state_of(MatchId(uuid4()))
    finally:
        await manager.shutdown()
