"""§4.4: a match recovered mid-duel comes back paused, with a real
`DuelPaused` in the log and the outage charged to nobody.

Every test here loads a real, persisted log through `recover` -- the same
way a server bootstrap does -- so all of them are integration-marked and
none of them constructs a `MatchState` by hand.
"""

from collections.abc import Sequence
from datetime import timedelta
from random import Random

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.actions import PauseDuel
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.events import (
    AnswerAccepted,
    BoardDealt,
    DuelPaused,
    DuelStarted,
    Event,
    MatchCreated,
    MatchStarted,
)
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import DuelPhase, MatchState
from podvinsya.runtime.match import MatchRuntime
from podvinsya.runtime.materialiser import Materialiser
from podvinsya.runtime.recovery import recover
from support.fakes import FakeCategoryBank, FakeClock, RecordingBroadcaster
from support.streams import BASE_TIME, Recorded, build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# --------------------------------------------------------------------------
# Persisting a (possibly truncated) stream, and folding the same prefix in
# memory. Local copies rather than a shared import -- matching
# tests/db/test_repository.py and tests/runtime/test_materialiser.py's own
# precedent of not sharing test fakes across files.
# --------------------------------------------------------------------------


async def _persist(
    sessions: async_sessionmaker[AsyncSession], match_id: MatchId, events: Sequence[Event]
) -> None:
    created = events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    uow = UnitOfWork(sessions)
    for offset, event in enumerate(events[1:]):
        async with uow.begin() as tx:
            await tx.append(
                match_id,
                expected_last_seq=offset + 1,
                events=(event,),
                operation_id=f"op-{offset}",
            )


def _fold_prefix(match_id: MatchId, events: Sequence[Event]) -> MatchState:
    genesis = events[0]
    assert isinstance(genesis, MatchCreated)
    return fold(create_initial_state(match_id, genesis.board, genesis.settings), events)


async def _persisted_prefix(
    sessions: async_sessionmaker[AsyncSession], recorded: Recorded, cut: int
) -> MatchState:
    """Persist `recorded.events[:cut]` and return the state it folds to."""
    truncated = recorded.events[:cut]
    state = _fold_prefix(recorded.state.id, truncated)
    await _persist(sessions, recorded.state.id, truncated)
    return state


def _cut_after(recorded: Recorded, event_type: type) -> int:
    """The index one past the first event of `event_type` -- the length of
    the prefix that ends right after it happened."""
    return next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type)) + 1


def _cut_before(recorded: Recorded, event_type: type) -> int:
    """The index of the first event of `event_type` -- the length of the
    prefix that ends right before it happens."""
    return next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type))


def _cut_after_duel_started_with_headroom(recorded: Recorded, minimum_ms: int) -> int:
    """Index one past the first `DuelStarted` whose answering player starts
    with at least `minimum_ms` of budget remaining.

    A duel's bonus budget grows with its attacking or defending group's
    cell count (§2.5), so a merged board eventually produces a duel whose
    answering player has more than the bare sixty-second base. Cutting
    there -- rather than after the very first duel's first answer, where
    both players sit at exactly the sixty-second base -- means a later
    minute-long gap leaves headroom instead of exactly exhausting the
    budget: the naive (no-anchor-override) mutant pauses with a reduced
    budget rather than expiring the duel outright, so a test built on this
    cut fails on the budgets themselves, not on the duel disappearing.
    """
    genesis = recorded.events[0]
    assert isinstance(genesis, MatchCreated)
    state = create_initial_state(recorded.state.id, genesis.board, genesis.settings)
    for index, event in enumerate(recorded.events):
        state = fold(state, (event,))
        if isinstance(event, DuelStarted):
            duel = state.duel
            assert duel is not None
            if duel.budgets.get(duel.answering) >= minimum_ms:
                return index + 1
    raise AssertionError(f"no duel in this stream ever had {minimum_ms}ms of headroom")


async def _recover(
    match_id: MatchId, sessions: async_sessionmaker[AsyncSession], clock: FakeClock
) -> MatchRuntime:
    """Build the ordinary collaborators and call `recover`, the way a
    server bootstrap would."""
    materialiser = Materialiser(clock, MatchRepository(sessions), FakeCategoryBank(), Random(0))
    return await recover(
        match_id,
        MatchRepository(sessions),
        UnitOfWork(sessions),
        materialiser,
        clock,
        Random(0),
        RecordingBroadcaster(),
    )


# --------------------------------------------------------------------------
# The pause itself
# --------------------------------------------------------------------------


async def test_a_match_mid_duel_comes_back_paused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A duel genuinely running (phase RUNNING, anchor set) when the log
    ends must come back paused (phase RUNNING, anchor None).

    Kills on: the RUNNING-and-anchored check being dropped or inverted, or
    the pause never being issued at all -- recovery would then hand back a
    runtime whose duel still carries its old anchor and is not `.paused`.
    """
    recorded = build_rich_stream()
    cut = _cut_after(recorded, AnswerAccepted)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is not None and prefix.duel.anchor is not None
    anchor = prefix.duel.anchor

    runtime = await _recover(recorded.state.id, sessions, FakeClock(anchor))

    duel = runtime.state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.RUNNING
    assert duel.paused, "a genuinely running duel must come back paused"


async def test_the_outage_is_charged_to_nobody(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The heart of §4.4. Persist a duel with an anchor, recover it a full
    minute of wall-time later, and assert both remainders are exactly what
    the log said before the crash. If this fails, a player loses a duel
    because a container restarted.

    Kills on: dropping the `at=duel.anchor` override in `_pause_at_anchor`
    (deciding the pause at `clock.now()` instead). The clock here reads a
    full minute past the anchor and the duel below is cut with enough
    budget headroom that a sixty-second overcharge does not expire it, so
    that mutant still produces a paused duel -- just one whose budgets
    come back sixty seconds short of `before` rather than identical to it.
    """
    recorded = build_rich_stream()
    cut = _cut_after_duel_started_with_headroom(recorded, minimum_ms=65_000)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is not None and prefix.duel.anchor is not None
    before = prefix.duel.budgets
    answering = prefix.duel.answering

    a_minute_later = prefix.duel.anchor + timedelta(minutes=1)
    runtime = await _recover(recorded.state.id, sessions, FakeClock(a_minute_later))

    duel = runtime.state.duel
    assert duel is not None
    assert duel.paused
    assert duel.budgets == before, (
        "the outage must be charged to nobody: both remainders must be exactly "
        "what the log said before the crash"
    )
    assert duel.answering == answering


async def test_the_pause_is_a_real_event_in_the_log(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """«Простой становится видимым событием.» The seq after recovery is
    one greater than the log had, and the new row is a `duel.paused`.

    Kills on: recovery folding the pause only in memory without appending
    it to the log -- the persisted log length would stay at `cut` and its
    last event would still be `AnswerAccepted`, not `DuelPaused`.
    """
    recorded = build_rich_stream()
    cut = _cut_after(recorded, AnswerAccepted)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is not None and prefix.duel.anchor is not None

    await _recover(recorded.state.id, sessions, FakeClock(prefix.duel.anchor))

    persisted = await MatchRepository(sessions).read_events(recorded.state.id)
    assert len(persisted) == cut + 1
    assert isinstance(persisted[-1], DuelPaused)
    assert persisted[-1].charged_ms == 0


async def test_a_match_that_was_already_paused_is_not_paused_again(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """`PauseDuel` on a paused duel is rejected by the domain. Recovery
    must not issue it -- a `SystemOrigin` rejection means the runtime's own
    model was wrong, and it would be, here.

    Kills on: the paused check accepting an already-paused duel (e.g.
    checking only `phase is RUNNING` and not the anchor too). Two
    independent guards stand between that mistake and a client: a paused
    duel's anchor is None, so `_pause_at_anchor`'s own precondition assert
    fires immediately; and even without that assert, the domain itself
    rejects a `PauseDuel` against an already-paused duel with
    `DUEL_PAUSED`, which `_pause_at_anchor` turns into a raised
    `RuntimeError`. Either way `await _recover` raises, so this test would
    fail right there. The log-length assertion below additionally kills a
    variant that catches and swallows that failure instead of propagating
    it.
    """
    recorded = build_rich_stream()
    cut = _cut_after(recorded, DuelPaused)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is not None and prefix.duel.paused

    runtime = await _recover(recorded.state.id, sessions, FakeClock(BASE_TIME))

    assert runtime.state.duel == prefix.duel
    persisted = await MatchRepository(sessions).read_events(recorded.state.id)
    assert len(persisted) == cut, "an already-paused duel must not gain a second DuelPaused"


# --------------------------------------------------------------------------
# States with nothing to pause
# --------------------------------------------------------------------------


async def test_a_match_with_no_duel_recovers_untouched(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Running, but between duels. Nothing to pause, nothing to append.

    Kills on: `_needs_pausing` reading `duel.phase` or `duel.paused` without
    first checking `duel is not None` -- against this duel-less state that
    raises `AttributeError` out of `await _recover`, uncaught. A version
    that gets the guard order right but the condition wrong in some other
    way would instead surface as `runtime.state` disagreeing with `prefix`
    or the persisted log gaining a row it should not have.
    """
    recorded = build_rich_stream()
    cut = _cut_after(recorded, MatchStarted)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is None

    runtime = await _recover(recorded.state.id, sessions, FakeClock(BASE_TIME))

    assert runtime.state == prefix
    persisted = await MatchRepository(sessions).read_events(recorded.state.id)
    assert len(persisted) == cut


async def test_a_match_in_setup_recovers_untouched(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Before `StartMatch`. Nothing to pause, nothing to append.

    The sibling to `test_a_match_with_no_duel_recovers_untouched`, pinned
    to a SETUP-status prefix rather than a RUNNING one: `_needs_pausing`
    has no `state.status` branch of its own, so this guards against one
    being added that mishandles SETUP. Kills on: any such branch that
    issues a pause here, or any change that returns a state other than the
    exact fold of what was persisted.
    """
    recorded = build_rich_stream()
    cut = _cut_before(recorded, BoardDealt)  # both secrets assigned, still SETUP
    prefix = await _persisted_prefix(sessions, recorded, cut)
    assert prefix.duel is None

    runtime = await _recover(recorded.state.id, sessions, FakeClock(BASE_TIME))

    assert runtime.state == prefix
    persisted = await MatchRepository(sessions).read_events(recorded.state.id)
    assert len(persisted) == cut


async def test_a_finished_match_recovers_untouched(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A finished match has no duel and nothing left to protect.

    Kills on: recovery mishandling a match with no live duel at the very
    end of a long log -- an off-by-one in how the prefix is folded, or an
    accidental append, would show up here as `runtime.state` disagreeing
    with `recorded.state` or the log gaining a row it should not have.
    """
    recorded = build_rich_stream()
    await _persist(sessions, recorded.state.id, recorded.events)
    assert recorded.state.duel is None

    runtime = await _recover(recorded.state.id, sessions, FakeClock(BASE_TIME))

    assert runtime.state == recorded.state
    persisted = await MatchRepository(sessions).read_events(recorded.state.id)
    assert len(persisted) == len(recorded.events)


# --------------------------------------------------------------------------
# The fold itself
# --------------------------------------------------------------------------


async def test_recovery_folds_the_log_exactly(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Before any pause is issued, the state must equal what the domain
    produced in memory. Plan 2 proved that for `load`; this proves the
    runtime does not perturb it on the way in.

    Compares the whole post-pause state against one hand-assembled by
    folding the same prefix and then deciding `PauseDuel` at the anchor
    directly -- not just the budgets `test_the_outage_is_charged_to_nobody`
    already covers. Kills on: recovery reordering or duplicating the fold
    (e.g. double-charging the pause, or folding onto the wrong starting
    state) -- any of those changes some field this full-state equality
    checks but the narrower budgets-only test would not catch on its own.
    """
    recorded = build_rich_stream()
    cut = _cut_after(recorded, AnswerAccepted)
    prefix = await _persisted_prefix(sessions, recorded, cut)
    duel = prefix.duel
    assert duel is not None and duel.anchor is not None

    expected_events = decide(prefix, PauseDuel(), DecisionContext(now=duel.anchor))
    expected = fold(prefix, expected_events)

    a_minute_later = duel.anchor + timedelta(minutes=1)
    runtime = await _recover(recorded.state.id, sessions, FakeClock(a_minute_later))

    assert runtime.state == expected
