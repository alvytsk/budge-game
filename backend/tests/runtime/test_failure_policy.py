"""Task 10: the failure story, end to end.

The previous nine tasks each tested one mechanism in isolation, mostly
against fakes. This module drives the whole stack -- `MatchManager`, the
commit path, the scheduler, recovery -- against a real, persisted log and
asserts the properties §11 names by them: the ones that only mean anything
once every piece is wired together.

Every test here builds its own small two-player match from scratch, one
command at a time through `MatchManager.submit`, rather than reusing
`support.streams.build_rich_stream()`. None of these tests care about
`build_rich_stream`'s content -- which groups merge, how much bonus budget
a duel carries -- only about a genuinely running duel with two players on
the smallest legal board, so driving it directly sidesteps the very
flakiness `build_rich_stream`'s own docs warn about (its event-type shape
is deterministic; its content is not, because target selection sorts
random UUIDs).

`_play_to_running_duel` has the same hazard in miniature: it too picks
`sorted(legal_targets(...))[0]`, over `GroupId`s the real `DealBoard` path
mints via `budge.runtime.materialiser.uuid4` -- unseeded, unlike the
shuffle that decides which cells land where. It is provably safe today
only because every group at the first attack is freshly dealt and
one-cell, and a one-cell group carries no time bonus (§2.5) regardless of
which one gets picked -- a safety that rests on a domain constant nobody
pinned. `_play_to_running_duel` pins `materialiser.uuid4` with the same
`deterministic_uuid4` Task 7 used for `streams.uuid4`, so which group is
actually attacked stops varying run to run.
"""

import asyncio
from datetime import timedelta
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from budge.db.repository import MatchRepository
from budge.db.store import UnitOfWork
from budge.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    DealBoard,
    DeclareAttack,
    JudgeCorrect,
    PauseDuel,
    ResumeDuel,
    StartDuel,
    StartMatch,
    UndoLastJudgement,
)
from budge.domain.board import BoardSize
from budge.domain.context import DecisionContext
from budge.domain.decide import decide
from budge.domain.errors import RejectionReason
from budge.domain.events import AnswerAccepted, DuelPaused, DuelResolved, MatchCreated
from budge.domain.evolve import fold
from budge.domain.genesis import create_initial_state
from budge.domain.ids import CategoryId, MatchId, PlayerId
from budge.domain.rules import legal_targets
from budge.domain.settings import MatchSettings
from budge.runtime import materialiser as materialiser_module
from budge.runtime.manager import MatchManager
from budge.runtime.materialiser import Materialiser
from budge.runtime.origins import Accepted, CommandOutcome, Rejected
from budge.services.ports import Broadcaster
from support.fakes import BreakingBroadcaster, FakeCategoryBank, FakeClock, RecordingBroadcaster
from support.streams import BASE_TIME, deterministic_uuid4

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

BOARD = BoardSize(3, 4)
SETTINGS = MatchSettings()
COLOURS = ("#e5484d", "#3b82f6")


# --------------------------------------------------------------------------
# Building a small, controlled match through the real manager.
# --------------------------------------------------------------------------


async def _create_genesis(sessions: async_sessionmaker[AsyncSession]) -> MatchId:
    match_id = MatchId(uuid4())
    created = MatchCreated(board=BOARD, settings=SETTINGS, player_count=2)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    return match_id


def _players() -> tuple[PlayerId, PlayerId]:
    return PlayerId(uuid4()), PlayerId(uuid4())


def _manager(
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    *,
    broadcaster: Broadcaster | None = None,
    seed: int = 0,
) -> MatchManager:
    def factory() -> Materialiser:
        return Materialiser(clock, MatchRepository(sessions), FakeCategoryBank(), Random(seed))

    return MatchManager(
        MatchRepository(sessions),
        UnitOfWork(sessions),
        factory,
        broadcaster if broadcaster is not None else RecordingBroadcaster(),
        clock,
        # None of these tests are about the watchdog (task 8 owns that);
        # several advance the fake clock by a minute or more in one jump
        # to move a duel past its deadline. The default 30s interval would
        # let the watchdog's own sweep -- itself a legitimate `sleep_until`
        # on this same clock -- wake and resolve a duel out from under a
        # test that is deliberately isolating a different code path (see
        # `test_a_late_judgement_loses_to_the_clock`). An interval far
        # longer than anything any test here advances the clock by removes
        # that source of interference entirely.
        watchdog_interval=timedelta(hours=1),
    )


async def _submit(manager: MatchManager, match_id: MatchId, command: Command) -> CommandOutcome:
    return await asyncio.wait_for(manager.submit(match_id, command), timeout=2)


async def _play_to_running_duel(
    manager: MatchManager,
    match_id: MatchId,
    players: tuple[PlayerId, PlayerId],
    monkeypatch: pytest.MonkeyPatch,
    *,
    uuid_start: int,
) -> list[Accepted]:
    """Add both players, assign secrets, deal, start the match, declare an
    attack and start the resulting duel -- eight accepted commands, ending
    with a duel genuinely RUNNING and anchored. Returns each command's
    `Accepted` outcome, in order, so a caller can count exactly how many
    committed batches this produced.

    `uuid_start` pins `budge.runtime.materialiser.uuid4` (the module
    `DealBoard`'s real path mints `GroupId`s through) for the duration of
    this call, so `sorted(legal_targets(...))[0]` below stops picking a
    different legal target from run to run -- see the module docstring.
    Each caller passes its own offset so none of this file's real,
    randomly-generated `MatchId`s could ever collide with a `GroupId`
    minted under the same pinned sequence.
    """
    monkeypatch.setattr(materialiser_module, "uuid4", deterministic_uuid4(start=uuid_start))
    outcomes: list[Accepted] = []
    for index, player_id in enumerate(players):
        outcome = await _submit(
            manager,
            match_id,
            AddPlayer(player_id=player_id, name=f"Player {index + 1}", colour=COLOURS[index]),
        )
        assert isinstance(outcome, Accepted), outcome
        outcomes.append(outcome)
    for player_id in players:
        outcome = await _submit(
            manager, match_id, AssignSecret(player_id=player_id, category=CategoryId(uuid4()))
        )
        assert isinstance(outcome, Accepted), outcome
        outcomes.append(outcome)

    outcome = await _submit(manager, match_id, DealBoard())
    assert isinstance(outcome, Accepted), outcome
    outcomes.append(outcome)
    outcome = await _submit(manager, match_id, StartMatch())
    assert isinstance(outcome, Accepted), outcome
    outcomes.append(outcome)

    runtime = manager.runtime_for(match_id)
    assert runtime is not None
    state = runtime.state
    attacker = state.current_player()
    attacking = next(
        group for group in state.groups_of(attacker) if legal_targets(state, group.id)
    )
    defending = sorted(legal_targets(state, attacking.id))[0]
    outcome = await _submit(
        manager,
        match_id,
        DeclareAttack(attacking_group=attacking.id, defending_group=defending),
    )
    assert isinstance(outcome, Accepted), outcome
    outcomes.append(outcome)

    outcome = await _submit(manager, match_id, StartDuel())
    assert isinstance(outcome, Accepted), outcome
    outcomes.append(outcome)

    return outcomes


# --------------------------------------------------------------------------
# «Ломающийся вещатель»
# --------------------------------------------------------------------------


async def test_a_match_survives_a_broadcaster_that_never_stops_failing(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11's «ломающийся вещатель». Play several commands with a
    broadcaster that raises every time, then assert the log holds every one
    of them and the state is what the domain would have produced.

    Kills on: `MatchRuntime._publish`'s try/except being removed or
    narrowed. Without it, the first successful publish crashes the
    consumer task in the background -- every later `_submit` here, wrapped
    in `asyncio.wait_for(..., timeout=2)`, would then time out instead of
    completing, and this test would fail loudly rather than hang. It also
    kills a broadcaster failure being routed into `_quarantine`: the
    `not runtime.quarantined` assertion below would fail directly.
    """
    match_id = await _create_genesis(sessions)
    players = _players()
    clock = FakeClock(BASE_TIME)
    broadcaster = BreakingBroadcaster()
    manager = _manager(sessions, clock, broadcaster=broadcaster)
    await manager.start(match_id)

    outcomes = await _play_to_running_duel(manager, match_id, players, monkeypatch, uuid_start=1)
    final = await _submit(manager, match_id, JudgeCorrect())
    assert isinstance(final, Accepted)

    runtime = manager.runtime_for(match_id)
    assert runtime is not None
    assert not runtime.quarantined, "a broadcaster failure must never quarantine the match"
    assert broadcaster.calls == len(outcomes) + 1, (
        "every committed command must still have tried to publish"
    )

    persisted = await MatchRepository(sessions).read_events(match_id)
    expected = fold(create_initial_state(match_id, BOARD, SETTINGS), persisted)
    assert runtime.state == expected, (
        "the log must hold every command's events, and the state must be "
        "exactly what folding it produces"
    )

    await manager.shutdown()


# --------------------------------------------------------------------------
# «Восстановление»
# --------------------------------------------------------------------------


async def test_a_recovered_match_replays_to_the_same_state(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11's «Восстановление». Play a match, drop the runtime, recover it
    from the log alone, and compare -- modulo the pause §4.4 adds.

    Goes through `MatchManager` twice -- once to build the match, once
    (fresh instance, same persisted log) to recover it -- rather than
    calling `recover()` directly the way `tests/runtime/test_recovery.py`
    does, so this is the higher-level, whole-stack version of that
    property: a manager that starts a match id it has never seen before
    must reconstruct it correctly through the ordinary `start` path.

    Kills on: any break in §4.4's pause-on-recovery -- dropping it,
    deciding it at the wrong instant, or applying it twice -- since
    `expected` is computed here independently of `recover()`, by deciding
    `PauseDuel` at the duel's own anchor against the pre-shutdown state
    directly. It also kills recovery losing or duplicating any of the
    eight commands played before the drop: `expected` is folded from
    `before_state`, which itself is the live runtime's own state after
    those eight commands, so a bug specific to *recovery*'s fold (as
    opposed to the ordinary commit path all nine tests here also exercise)
    would show up as `recovered.state != expected` even though both sides
    agree on everything before the pause.
    """
    match_id = await _create_genesis(sessions)
    players = _players()
    clock = FakeClock(BASE_TIME)
    manager_a = _manager(sessions, clock)
    await manager_a.start(match_id)
    await _play_to_running_duel(manager_a, match_id, players, monkeypatch, uuid_start=1_000)

    runtime_a = manager_a.runtime_for(match_id)
    assert runtime_a is not None
    before_state = runtime_a.state
    duel = before_state.duel
    assert duel is not None and duel.anchor is not None, (
        "recovery only pauses a genuinely running duel -- the test needs one"
    )

    # "Drop the runtime": tear down the consumer and the armed deadline
    # without issuing anything new. The persisted log is all that survives.
    await manager_a.shutdown()

    # Simulate real downtime between the drop and the recovery -- §4.4
    # decides the pause at the duel's own anchor, not at whatever "now" is
    # when recovery runs, so this must not change the result at all.
    await clock.advance_to(clock.now() + timedelta(minutes=5))

    manager_b = _manager(sessions, clock)
    recovered = await manager_b.start(match_id)

    expected_events = decide(before_state, PauseDuel(), DecisionContext(now=duel.anchor))
    expected = fold(before_state, expected_events)

    assert recovered.state == expected

    await manager_b.shutdown()


# --------------------------------------------------------------------------
# «Гонка дедлайна и судейства»
# --------------------------------------------------------------------------


async def test_a_late_judgement_loses_to_the_clock(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11's «Гонка дедлайна и судейства». Submit `JudgeCorrect` after the
    deadline has passed and assert the duel resolved as an expiry. §4.2:
    «Опоздавшее „Верно“ не воскрешает проигранную дуэль.»

    `runtime.stop()` cancels the scheduler's own timer *before* the clock
    ever moves past the deadline. Without it, `clock.advance_to` would wake
    the scheduler's sleeping task, whose `fire` callback runs synchronously
    (no `await` of its own) as soon as the loop gives it a turn -- almost
    certainly during `advance_to`'s own `settle()` -- and `ExpireTimer`
    would very likely reach and resolve the duel through its *own* code
    path before our own `JudgeCorrect` is even submitted. That would still
    leave the duel resolved as an expiry, but for the wrong reason: it
    would pass even if `_judge_correct`'s own `is_expired` check were
    deleted, because `ExpireTimer` alone would have covered for it. Killing
    the scheduler first is what makes the property under test -- that
    `decide` itself, not the scheduler, is what makes the clock
    authoritative over the command -- the only way this test can pass.
    `_manager`'s own oversized `watchdog_interval` closes the same gap a
    second way: task 8's watchdog treats "RUNNING, unpaused, no timer
    armed" -- exactly what `runtime.stop()` deliberately produces here --
    as the fault it exists to catch, and would otherwise resolve the duel
    itself the moment a sweep lands inside the same jump forward.

    Kills on: removing (or inverting) the `is_expired(duel, ctx.now)` check
    at the top of `_judge_correct`. Without it, this late `JudgeCorrect`
    would be accepted as an ordinary correct answer: `outcome.events` would
    hold an `AnswerAccepted` instead of a `DuelResolved`, `runtime.state
    .duel` would stay very much alive (RUNNING, answering flipped to the
    other player), and every assertion below would fail.
    """
    match_id = await _create_genesis(sessions)
    players = _players()
    clock = FakeClock(BASE_TIME)
    manager = _manager(sessions, clock)
    await manager.start(match_id)
    await _play_to_running_duel(manager, match_id, players, monkeypatch, uuid_start=2_000)

    runtime = manager.runtime_for(match_id)
    assert runtime is not None
    duel = runtime.state.duel
    assert duel is not None and duel.anchor is not None
    answering = duel.answering
    deadline = duel.anchor + timedelta(milliseconds=duel.budgets.get(answering))

    runtime.stop()
    await clock.settle()
    assert runtime.scheduler.deadline_id is None, (
        "the scheduler's own timer must be cancelled before we move time -- the watchdog also "
        "sleeps on this clock, so `clock.pending()` alone cannot tell the two apart"
    )

    await clock.advance_to(deadline + timedelta(seconds=1))
    outcome = await _submit(manager, match_id, JudgeCorrect())

    assert isinstance(outcome, Accepted)
    assert not any(isinstance(event, AnswerAccepted) for event in outcome.events), (
        "the clock's authority means no correct answer was ever accepted"
    )
    resolved = next(event for event in outcome.events if isinstance(event, DuelResolved))
    assert resolved.loser == answering, (
        "a late 'correct' must lose exactly like a timeout, not win like an on-time answer"
    )
    assert runtime.state.duel is None, "the duel must have resolved, one way or the other"

    await manager.shutdown()


# --------------------------------------------------------------------------
# «Пауза»
# --------------------------------------------------------------------------


async def test_pause_charges_only_the_unfrozen_intervals(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11: «суммарное списанное время равно сумме незамороженных
    интервалов». Pause and resume several times across a duel and check
    the arithmetic against the clock the test itself drove.

    Kills on: `_charge` measuring elapsed time from the duel's original
    start rather than from the current anchor (a resume that fails to
    reset the clock's reference point) -- the second and third
    `paused.charged_ms` assertions below would then include the preceding
    frozen interval too, and fail immediately, before the cumulative
    check even runs. It also kills `_resume_duel` charging anything itself,
    or failing to set a fresh anchor: either would show up as the
    cumulative `charged_total` disagreeing with `total_unfrozen_ms`.
    """
    match_id = await _create_genesis(sessions)
    players = _players()
    clock = FakeClock(BASE_TIME)
    manager = _manager(sessions, clock)
    await manager.start(match_id)
    await _play_to_running_duel(manager, match_id, players, monkeypatch, uuid_start=3_000)

    runtime = manager.runtime_for(match_id)
    assert runtime is not None
    duel = runtime.state.duel
    assert duel is not None
    answering = duel.answering
    base_remaining = duel.budgets.get(answering)

    unfrozen_intervals = (timedelta(seconds=3), timedelta(seconds=2), timedelta(seconds=1))
    frozen_intervals = (timedelta(seconds=20), timedelta(seconds=15))

    total_unfrozen_ms = 0
    for index, unfrozen in enumerate(unfrozen_intervals):
        await clock.advance_to(clock.now() + unfrozen)
        unfrozen_ms = int(unfrozen.total_seconds() * 1000)
        total_unfrozen_ms += unfrozen_ms

        outcome = await _submit(manager, match_id, PauseDuel())
        assert isinstance(outcome, Accepted)
        paused = next(event for event in outcome.events if isinstance(event, DuelPaused))
        assert paused.charged_ms == unfrozen_ms, (
            "each pause must charge exactly its own unfrozen interval, nothing frozen before it"
        )

        if index < len(frozen_intervals):
            await clock.advance_to(clock.now() + frozen_intervals[index])  # nobody is charged
            outcome = await _submit(manager, match_id, ResumeDuel())
            assert isinstance(outcome, Accepted)

    final_duel = runtime.state.duel
    assert final_duel is not None
    assert final_duel.answering == answering, "no judgement ever ran; the answering side is fixed"
    charged_total = base_remaining - final_duel.budgets.get(answering)
    assert charged_total == total_unfrozen_ms, (
        "the total charged must equal the sum of unfrozen intervals, and nothing else"
    )

    await manager.shutdown()


# --------------------------------------------------------------------------
# «Отмена»
# --------------------------------------------------------------------------


async def test_a_chain_of_undos_returns_the_state_bit_for_bit(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11's «Отмена». Judge three times, undo three times, and compare the
    duel against the snapshot taken before the first judgement.

    Kills on: any drift in the journal `Materialiser._journal` rebuilds
    from the log, or in how `_undo`/`evolve` apply a `JudgementUndone` --
    a wrong `seq`, a stale `image_index`, a budget or anchor not restored
    exactly -- any of those would leave `runtime.state.duel != before`
    after the third undo. The trailing `NOTHING_TO_UNDO` assertion kills a
    chain that walks one judgement too far back, past the start of the
    duel (§3.7).

    The clock never moves in this test. That is deliberate, not an
    oversight: `_undo` re-anchors an undone duel at whatever `now` is when
    the undo itself is decided (§4.1 -- a live game clock cannot be
    rewound to a past instant, only its budget can), so a chain of undos
    only returns the duel bit for bit, anchor included, when every
    judgement and every undo shares the same `now` as the original
    `StartDuel`. Advancing the clock between judgements would still prove
    budgets and answering restore correctly, but the trailing `anchor`
    would then differ from `before` by construction, which is a fact about
    the clock this test drives, not about undo being wrong.
    """
    match_id = await _create_genesis(sessions)
    players = _players()
    clock = FakeClock(BASE_TIME)
    manager = _manager(sessions, clock)
    await manager.start(match_id)
    await _play_to_running_duel(manager, match_id, players, monkeypatch, uuid_start=4_000)

    runtime = manager.runtime_for(match_id)
    assert runtime is not None
    before = runtime.state.duel
    assert before is not None

    for _ in range(3):
        outcome = await _submit(manager, match_id, JudgeCorrect())
        assert isinstance(outcome, Accepted)

    after_judging = runtime.state.duel
    assert after_judging is not None and after_judging != before, (
        "the test needs three judgements to have actually moved the duel"
    )

    for _ in range(3):
        outcome = await _submit(manager, match_id, UndoLastJudgement())
        assert isinstance(outcome, Accepted)

    assert runtime.state.duel == before, "a chain of three undos must return the duel bit for bit"

    last = await _submit(manager, match_id, UndoLastJudgement())
    assert isinstance(last, Rejected)
    assert last.reason is RejectionReason.NOTHING_TO_UNDO, (
        "undo must not cross the start of the duel"
    )

    await manager.shutdown()
