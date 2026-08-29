"""§6.3's table, one test per row.

Every fake here fails in a specific, named way. A single "the database
broke" fake would let three different rows of the table collapse into one
untested branch.

These tests drive fakes, not PostgreSQL: the point is the classification,
and a real database will not produce a serialisation failure on demand. No
test here touches a database, so none of them carry `pytest.mark.integration`.

Two clocks are used, deliberately. `support.fakes.FakeClock` genuinely parks
a sleeper on an `asyncio.Event`, which is exactly right for the one test that
proves the backoff goes through the clock port at all
(`test_the_retry_delay_is_taken_from_the_clock`) — and exactly the wrong
amount of ceremony for every other retry test, which only cares that a
second attempt happened, not how long the wait was. Those use `_NoWaitClock`,
a local double whose `sleep_until` returns immediately without touching real
time.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

import budge.runtime.commit as commit
from budge.db.errors import ConcurrentModification
from budge.db.repository import LoadedMatch
from budge.domain.actions import AddPlayer, AssignSecret, Command, CreateMatch, DealBoard
from budge.domain.board import BoardSize
from budge.domain.context import DealPlan, DecisionContext
from budge.domain.decide import decide
from budge.domain.errors import RejectionReason
from budge.domain.evolve import fold
from budge.domain.events import BoardDealt, Event, MatchCreated
from budge.domain.genesis import create_initial_state
from budge.domain.ids import CategoryId, MatchId, PlayerId
from budge.domain.settings import MatchSettings
from budge.domain.state import MatchState, MatchStatus, Player
from budge.runtime.commit import CommitPath
from budge.runtime.materialiser import Materialiser
from budge.runtime.origins import (
    Accepted,
    Failed,
    NoOp,
    QueuedCommand,
    Rejected,
    SystemOrigin,
)
from budge.services.ports import Reconciliation, RuntimeCode, Transaction
from support.fakes import FakeCategoryBank, FakeClock

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
_BOARD = BoardSize(3, 4)
_SETTINGS = MatchSettings()
_PLAYER_COUNT = 2


# --------------------------------------------------------------------------
# Shared doubles
# --------------------------------------------------------------------------


class _NoWaitClock:
    """`sleep_until` returns instantly: this is a clock, not a scheduler,
    for tests that need several attempts but do not care how long any
    single backoff lasted."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        self._now = when


class _NullRepository:
    """Every command exercised in this module reaches the materialiser
    through `DealBoard` or a command the materialiser gives a bare context
    to — never `UndoLastJudgement`, the only case that reads the
    repository. Any call here is a test bug, not a legitimate path."""

    async def create(self, match_id: MatchId, event: MatchCreated, *, operation_id: str) -> None:
        raise NotImplementedError("no test in this module reads the repository")

    async def read_events(self, match_id: MatchId) -> tuple[Event, ...]:
        raise NotImplementedError("no test in this module reads the repository")

    async def load(self, match_id: MatchId) -> LoadedMatch:
        raise NotImplementedError("no test in this module reads the repository")


def _materialiser(*, random: Random | None = None) -> Materialiser:
    return Materialiser(FakeClock(NOW), _NullRepository(), FakeCategoryBank(), random or Random(0))


class _FakeDriverError(Exception):
    """Stands in for the exception asyncpg's own driver raises. Populates
    `.sqlstate`, confirmed by experiment to be the attribute asyncpg
    actually sets (`.pgcode` does not exist on its exceptions)."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _dbapi_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("statement", {}, _FakeDriverError(sqlstate))


class _RecordingTransaction:
    """Appends never fail; every call is recorded in call order."""

    def __init__(self, sink: list[tuple[MatchId, int, tuple[Event, ...], str]]) -> None:
        self._sink = sink

    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        self._sink.append((match_id, expected_last_seq, tuple(events), operation_id))


class _RecordingUoW:
    """Nothing ever fails. `committed` records what got appended, so a
    test can check both whether anything was appended and, when several
    calls happen, in what order."""

    def __init__(self) -> None:
        self.committed: list[tuple[MatchId, int, tuple[Event, ...], str]] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Transaction]:
        yield _RecordingTransaction(self.committed)

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        raise AssertionError("reconcile must not be called when the commit never raised")


class _ConcurrentTransaction:
    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        raise ConcurrentModification(match_id, expected_last_seq)


class _ConcurrentModificationUoW:
    """`append`'s optimistic guard matches zero rows: someone else already
    advanced this match past what `decide` saw."""

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Transaction]:
        yield _ConcurrentTransaction()

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        raise AssertionError("a concurrent-modification body failure must not reconcile")


class _FlakyTransaction:
    def __init__(self, owner: "_FlakyBodyUoW", *, should_fail: bool) -> None:
        self._owner = owner
        self._should_fail = should_fail

    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        # Recorded before the raise, deliberately: a test that wants to know
        # what operation_id a *failing* attempt used still needs to see it.
        self._owner.operation_ids_seen.append(operation_id)
        if self._should_fail:
            raise _dbapi_error(self._owner.sqlstate)
        self._owner.committed.append((match_id, expected_last_seq, tuple(events), operation_id))


class _FlakyBodyUoW:
    """`append` raises a `DBAPIError` carrying a chosen SQLSTATE for the
    first `fails` attempts, then behaves like `_RecordingUoW`. Models a
    transaction that rolled back cleanly, because the failure happened
    while `append` was still running — before any COMMIT was sent, so
    reconciliation must never be consulted."""

    def __init__(self, sqlstate: str, *, fails: int) -> None:
        self.sqlstate = sqlstate
        self._fails = fails
        self.attempts = 0
        self.committed: list[tuple[MatchId, int, tuple[Event, ...], str]] = []
        self.operation_ids_seen: list[str] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Transaction]:
        self.attempts += 1
        yield _FlakyTransaction(self, should_fail=self.attempts <= self._fails)

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        raise AssertionError("an unambiguous rollback must never reconcile")


class _AmbiguousCommitUoW:
    """`append` itself always succeeds; exiting the transaction raises for
    the first `len(results)` attempts — as if the COMMIT reached the server
    but its acknowledgement never came back. `reconcile` is scripted, one
    result per ambiguous attempt, so a script of `[ABSENT]` can be followed
    by a clean second attempt the way a real retry would be."""

    def __init__(self, results: Sequence[Reconciliation]) -> None:
        self._results = list(results)
        self._attempt = 0
        self.committed: list[tuple[MatchId, int, tuple[Event, ...], str]] = []
        self.reconciled_with: list[tuple[MatchId, str, int, tuple[Event, ...]]] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Transaction]:
        self._attempt += 1
        tx = _RecordingTransaction(self.committed)
        if self._attempt <= len(self._results):
            yield tx
            raise _dbapi_error("08006")  # connection lost, mid-commit
        else:
            yield tx

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        result = self._results[len(self.reconciled_with)]
        self.reconciled_with.append((match_id, operation_id, expected_last_seq, tuple(events)))
        return result


class _RaisingMaterialiser(Materialiser):
    """Every `build` call raises, unconditionally. `calls` lets a test
    prove the attempt was not retried."""

    def __init__(self) -> None:
        super().__init__(FakeClock(NOW), _NullRepository(), FakeCategoryBank(), Random(0))
        self.calls = 0

    async def build(
        self, state: MatchState, command: Command, tx: Transaction, *, at: datetime | None = None
    ) -> DecisionContext:
        self.calls += 1
        raise RuntimeError("materialiser blew up")


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------


def _create_match_request(*, seq: int = 0) -> tuple[MatchState, QueuedCommand]:
    """`CreateMatch` at `seq=0` always succeeds, producing exactly one
    event, without touching the materialiser's bank or repository at all —
    a convenient "the happy path" command for tests whose point is the
    commit machinery, not any particular domain rule."""
    state = MatchState(
        id=MatchId(uuid4()),
        seq=seq,
        status=MatchStatus.SETUP,
        board=_BOARD,
        settings=_SETTINGS,
        player_count=_PLAYER_COUNT,
    )
    command = CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT)
    queued = QueuedCommand.issue(command, SystemOrigin("test"))
    return state, queued


def _expected_match_created() -> MatchCreated:
    return MatchCreated(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT)


def _deal_ready_state() -> MatchState:
    """Two players, each with a secret assigned, on a board that divides
    evenly between them — everything `DealBoard` needs to succeed, built by
    actually running `decide`/`fold` rather than hand-assembled, so it is
    guaranteed to be a state the domain itself would produce."""
    state = create_initial_state(MatchId(uuid4()), _BOARD, _SETTINGS)

    def apply(command: Command, **ctx_kwargs: object) -> None:
        nonlocal state
        ctx = DecisionContext(now=NOW, **ctx_kwargs)  # type: ignore[arg-type]
        state = fold(state, decide(state, command, ctx))

    apply(CreateMatch(board=_BOARD, settings=_SETTINGS, player_count=_PLAYER_COUNT))
    players = (PlayerId(uuid4()), PlayerId(uuid4()))
    for index, player_id in enumerate(players):
        apply(AddPlayer(player_id=player_id, name=f"Player {index}", colour="#000000"))
        apply(AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    return state


def _deal_board_request() -> tuple[MatchState, QueuedCommand]:
    state = _deal_ready_state()
    queued = QueuedCommand.issue(DealBoard(), SystemOrigin("test"))
    return state, queued


# --------------------------------------------------------------------------
# §6.3's table
# --------------------------------------------------------------------------


async def test_a_rejection_from_decide_leaves_the_runtime_healthy() -> None:
    """«отказ из decide → откат, ответ origin, состояние не тронуто,
    рантайм здоров». The transaction must have rolled back and nothing
    may have been appended."""
    state, queued = _create_match_request(seq=7)  # nonzero seq: _create_match rejects
    uow = _RecordingUoW()
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))

    outcome = await path.run(state, queued)

    assert outcome == Rejected(RejectionReason.WRONG_STATUS)
    assert uow.committed == [], "a rejection must not append anything"


async def test_a_content_shortfall_is_an_ordinary_refusal() -> None:
    """«нехватка контента при отборе → обычный отказ, не авария». The
    outcome names CONTENT_UNAVAILABLE and the caller is told; the match is
    not quarantined.

    The state must otherwise be deal-ready -- full roster, every secret
    assigned -- or the materialiser's own totality guard (Critical 1) would
    short-circuit on the incomplete roster before ever reaching the bank,
    and this test would stop exercising content exhaustion at all.
    """
    state = _deal_ready_state()
    queued = QueuedCommand.issue(DealBoard(), SystemOrigin("test"))
    bank = FakeCategoryBank(exhaust_after=0)
    materialiser = Materialiser(FakeClock(NOW), _NullRepository(), bank, Random(0))
    path = CommitPath(_RecordingUoW(), materialiser, _NoWaitClock(NOW), Random(0))

    outcome = await path.run(state, queued)

    assert isinstance(outcome, Failed)
    assert outcome.code is RuntimeCode.CONTENT_UNAVAILABLE


async def test_a_serialisation_failure_is_retried() -> None:
    """SQLSTATE 40001. The second attempt must succeed and the outcome
    must be a plain commit — the caller never learns there was a retry."""
    uow = _FlakyBodyUoW("40001", fails=1)
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Accepted((_expected_match_created(),))
    assert uow.attempts == 2


async def test_a_deadlock_is_retried() -> None:
    """SQLSTATE 40P01, the other half of «известный откат БД»."""
    uow = _FlakyBodyUoW("40P01", fails=1)
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Accepted((_expected_match_created(),))
    assert uow.attempts == 2


async def test_a_retry_re_materialises_and_re_decides() -> None:
    """«Повтор переигрывает попытку целиком … переиспользование уже
    материализованного тиража означало бы выбор под блокировками, которых
    больше нет.» Assert the materialiser was called once per attempt, not
    once for the command."""

    class _CountingMaterialiser(Materialiser):
        def __init__(self) -> None:
            super().__init__(FakeClock(NOW), _NullRepository(), FakeCategoryBank(), Random(0))
            self.calls = 0

        async def build(
            self,
            state: MatchState,
            command: Command,
            tx: Transaction,
            *,
            at: datetime | None = None,
        ) -> DecisionContext:
            self.calls += 1
            return await super().build(state, command, tx, at=at)

    materialiser = _CountingMaterialiser()
    uow = _FlakyBodyUoW("40001", fails=1)
    path = CommitPath(uow, materialiser, _NoWaitClock(NOW), Random(0))
    state, queued = _deal_board_request()

    outcome = await path.run(state, queued)

    assert isinstance(outcome, Accepted)
    assert materialiser.calls == 2, "one materialise per attempt, not one for the whole command"


async def test_a_retry_keeps_the_operation_id_and_nothing_else() -> None:
    """«стабилен только operation_id». If the retry minted a new one,
    reconciliation after an ambiguous commit would look for a batch that
    was never written under that name."""
    uow = _FlakyBodyUoW("40001", fails=1)
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    await path.run(state, queued)

    assert uow.operation_ids_seen == [queued.operation_id, queued.operation_id], (
        "both attempts must carry the one operation_id minted at QueuedCommand.issue"
    )


async def test_a_retry_that_legitimately_decides_differently_is_accepted() -> None:
    """«Переигрывание может законно дать другие события — это корректно,
    потому что ничего не было закоммичено.» A re-deal draws a different
    shuffle; the attempt must not compare the two and panic.

    "Does not crash" is not enough to prove that: the assertion must fail
    if the two attempts happened to decide identically. `Materialiser`'s
    `Random` is shared and mutated across `build` calls, so the second
    attempt's shuffle is expected to diverge from the first's -- this
    records both deals and checks the divergence directly, comparing only
    each cell's owner (the property "the host's shuffle button" is about),
    since group_id and category are always fresh per call regardless of
    whether the shuffle itself changed and would make the comparison true
    for the wrong reason.
    """

    class _RecordingMaterialiser(Materialiser):
        def __init__(self) -> None:
            super().__init__(FakeClock(NOW), _NullRepository(), FakeCategoryBank(), Random(0))
            self.deals: list[DealPlan] = []

        async def build(
            self,
            state: MatchState,
            command: Command,
            tx: Transaction,
            *,
            at: datetime | None = None,
        ) -> DecisionContext:
            ctx = await super().build(state, command, tx, at=at)
            assert ctx.deal is not None
            self.deals.append(ctx.deal)
            return ctx

    def layout(plan: DealPlan) -> tuple[tuple[object, object], ...]:
        return tuple(sorted((dealt.cell, dealt.owner) for dealt in plan.cells))

    materialiser = _RecordingMaterialiser()
    uow = _FlakyBodyUoW("40001", fails=1)
    path = CommitPath(uow, materialiser, _NoWaitClock(NOW), Random(0))
    state, queued = _deal_board_request()

    outcome = await path.run(state, queued)

    assert isinstance(outcome, Accepted)
    assert len(outcome.events) == 1
    accepted = outcome.events[0]
    assert isinstance(accepted, BoardDealt)

    assert len(materialiser.deals) == 2, "one deal per attempt, the failed one and the accepted one"
    first_attempt, second_attempt = materialiser.deals
    assert layout(first_attempt) != layout(second_attempt), (
        "the two attempts must genuinely reshuffle, not repeat the same layout -- "
        "otherwise this test cannot tell 'legitimately differs' from 'happened to crash'"
    )
    assert accepted.cells == second_attempt.cells, (
        "the committed batch must be the second attempt's deal, not the discarded first one"
    )


async def test_retries_are_bounded_and_then_the_database_is_unavailable() -> None:
    """«БД недоступна после повторов → карантин». The outcome says
    DATABASE_UNAVAILABLE; quarantining is the caller's move, not this
    function's."""
    uow = _FlakyBodyUoW("40001", fails=99)  # never recovers
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0), max_attempts=3)
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Failed(RuntimeCode.DATABASE_UNAVAILABLE, "retries exhausted")
    assert uow.attempts == 3


async def test_an_error_out_of_the_commit_itself_reconciles() -> None:
    """An error raised while the body was still running rolled back and is
    unambiguous. One raised as the transaction closes is not: the COMMIT
    may have reached the server. That is the case that reconciles."""
    uow = _AmbiguousCommitUoW([Reconciliation.MATCHED])
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Accepted((_expected_match_created(),))
    assert len(uow.reconciled_with) == 1
    match_id, operation_id, expected_last_seq, events = uow.reconciled_with[0]
    assert match_id == state.id
    assert operation_id == queued.operation_id
    assert expected_last_seq == state.seq
    assert events == (_expected_match_created(),)


async def test_a_reconciled_match_is_reported_as_committed() -> None:
    """«Совпало — коммит прошёл, обработка продолжается со свёртки.»

    MATCHED must not be confused with ABSENT: if it were mapped to _Retry
    the way ABSENT is, this test's outcome would still read Accepted --
    the scripted fake has no second ambiguous result, so the retried
    attempt would complete cleanly on its own -- but the match would have
    appended the same command to the log twice. Pinning the commit count
    at exactly one is what tells the two apart.
    """
    uow = _AmbiguousCommitUoW([Reconciliation.MATCHED])
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Accepted((_expected_match_created(),))
    assert len(uow.committed) == 1, (
        "MATCHED must be reported as-is, not retried -- retrying would append "
        "the same command a second time"
    )


async def test_a_reconciled_absence_is_retried() -> None:
    """ABSENT means the batch never landed, so the attempt is replayed —
    collapsing it into a divergence would quarantine a healthy match."""
    uow = _AmbiguousCommitUoW([Reconciliation.ABSENT])
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Accepted((_expected_match_created(),))
    assert len(uow.committed) == 2, "the replay must append again, on the second attempt"


async def test_a_reconciled_divergence_breaks_the_match() -> None:
    """«Любое расхождение — карантин, никаких „почти совпало“.»"""
    uow = _AmbiguousCommitUoW([Reconciliation.DIVERGED])
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Failed(RuntimeCode.INTERNAL, "the log diverged from this batch")


async def test_an_exception_in_decide_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """«исключение в decide / evolve → карантин, без повторов». A bug in
    the domain reproduces exactly on replay; retrying it three times just
    delays the diagnosis."""
    calls = 0

    def _raising_decide(
        state: MatchState, command: Command, ctx: DecisionContext
    ) -> tuple[Event, ...]:
        nonlocal calls
        calls += 1
        raise RuntimeError("a domain bug")

    monkeypatch.setattr(commit, "decide", _raising_decide)
    uow = _RecordingUoW()
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert isinstance(outcome, Failed)
    assert outcome.code is RuntimeCode.INTERNAL
    assert calls == 1, "a bug that reproduces exactly on replay must not be retried"


async def test_an_exception_in_the_materialiser_breaks_the_match() -> None:
    """«исключение в материализаторе → карантин»."""
    materialiser = _RaisingMaterialiser()
    uow = _RecordingUoW()
    path = CommitPath(uow, materialiser, _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert isinstance(outcome, Failed)
    assert outcome.code is RuntimeCode.INTERNAL
    assert materialiser.calls == 1, "a materialiser fault must not be retried either"


async def test_a_concurrent_modification_breaks_the_match() -> None:
    """Someone else advanced last_seq past what this attempt's decide saw.
    §6.3 has no retry row for it, and retrying would append events decided
    against state that is no longer current."""
    uow = _ConcurrentModificationUoW()
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))
    state, queued = _create_match_request()

    outcome = await path.run(state, queued)

    assert outcome == Failed(RuntimeCode.INTERNAL, "another writer advanced this match")


async def test_no_events_means_nothing_is_appended() -> None:
    """§6.2 resolves a no-op without opening the log at all."""
    player_id = PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = MatchState(
        id=MatchId(uuid4()),
        seq=3,
        status=MatchStatus.SETUP,
        board=_BOARD,
        settings=_SETTINGS,
        player_count=_PLAYER_COUNT,
        players=(Player(player_id, "A", "#fff"),),
        secrets={player_id: category},
    )
    queued = QueuedCommand.issue(
        AssignSecret(player_id=player_id, category=category), SystemOrigin("test")
    )
    uow = _RecordingUoW()
    path = CommitPath(uow, _materialiser(), _NoWaitClock(NOW), Random(0))

    outcome = await path.run(state, queued)

    assert outcome == NoOp()
    assert uow.committed == []


async def test_the_retry_delay_is_taken_from_the_clock() -> None:
    """No test may wait on the wall. The backoff sleeps through the clock
    port, so a fake clock makes the delay a value the test controls."""
    clock = FakeClock(NOW)
    uow = _FlakyBodyUoW("40001", fails=1)
    path = CommitPath(uow, _materialiser(), clock, Random(0))
    state, queued = _create_match_request()

    task = asyncio.create_task(path.run(state, queued))
    await clock.settle()
    assert clock.pending() == 1, "the retry must be parked on the clock, not on real time"

    await clock.advance_to(NOW + timedelta(days=1))
    outcome = await task

    assert outcome == Accepted((_expected_match_created(),))


async def test_no_backoff_sleeps_after_the_final_attempt() -> None:
    """Important 5: sleeping a full backoff before returning
    DATABASE_UNAVAILABLE just delays a diagnosis that is already final --
    with the real clock, dead latency right before a quarantine. The fake
    clock masked this (a retry test only needs `advance_to` called once
    per genuine backoff), which is exactly why it went unnoticed; this
    proves the terminal attempt never parks on one at all."""
    clock = FakeClock(NOW)
    uow = _FlakyBodyUoW("40001", fails=99)  # never recovers
    path = CommitPath(uow, _materialiser(), clock, Random(0), max_attempts=3)
    state, queued = _create_match_request()

    task = asyncio.create_task(path.run(state, queued))

    # Exactly two backoffs cover three attempts. If the guard were missing,
    # a third `sleep_until` would park the task forever after this loop --
    # nothing below advances the clock a third time -- and the
    # `wait_for` below would time out instead of completing.
    for _ in range(2):
        await clock.settle()
        assert clock.pending() == 1, "each retry but the last must park on a real backoff"
        await clock.advance_to(clock.now() + timedelta(days=1))

    outcome = await asyncio.wait_for(task, timeout=2)

    assert outcome == Failed(RuntimeCode.DATABASE_UNAVAILABLE, "retries exhausted")
    assert uow.attempts == 3
    assert clock.pending() == 0, "the final, terminal attempt must not sleep a backoff nobody uses"
