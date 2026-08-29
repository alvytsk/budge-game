"""The materialiser hands the domain every value it cannot compute itself.

The journal tests are the ones that matter. Plan 1 left a carry-forward in
so many words: «undo never crosses into the previous duel» is enforced by
whoever assembles `ctx.duel_journal`, not by the domain — the domain reads
what it is given. This is that assembler.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
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
    CreateMatch,
    DealBoard,
    DeclareAttack,
    ExpireTimer,
    JudgeCorrect,
    JudgePass,
    PauseDuel,
    StartDuel,
    StartMatch,
    UndoLastJudgement,
)
from budge.domain.board import BoardSize
from budge.domain.context import DealPlan, DecisionContext
from budge.domain.decide import decide
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.evolve import fold
from budge.domain.events import (
    AnswerAccepted,
    AttackDeclared,
    BoardDealt,
    DuelStarted,
    Event,
    JudgementUndone,
    MatchCreated,
    PassUsed,
)
from budge.domain.genesis import create_initial_state
from budge.domain.ids import CategoryId, ImageId, MatchId, PlayerId
from budge.domain.rules import legal_targets
from budge.domain.settings import MatchSettings
from budge.domain.state import MatchState, MatchStatus
from budge.runtime.materialiser import IMAGE_PACK_SIZE, Materialiser
from budge.services.ports import ContentExhausted, Transaction
from support.fakes import FakeCategoryBank, FakeClock
from support.streams import IMAGES_PER_DUEL, Recorded, build_rich_stream, make_deal

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

# A base time distinct from NOW: NOW is what the materialiser's own FakeClock
# reads, and using it for event timestamps too would make it impossible to
# tell "the clock the materialiser was built with" from "the anchor already
# recorded in the log" in a test that asserts on both.
_BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _materialiser(sessions: async_sessionmaker[AsyncSession]) -> Materialiser:
    return Materialiser(FakeClock(NOW), MatchRepository(sessions), FakeCategoryBank(), Random(0))


class _NullTransaction:
    """A trivial stand-in for `Transaction`. `FakeCategoryBank` ignores its
    `tx` argument entirely, so nothing here needs to behave like a real one —
    every deal- and image-drawing test in this module goes through
    `FakeCategoryBank`, never through a real `TransactionContext`."""

    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        raise NotImplementedError("FakeCategoryBank ignores tx; no test should reach this")


def _tx() -> Transaction:
    return _NullTransaction()


async def _persist(
    sessions: async_sessionmaker[AsyncSession], match_id: MatchId, events: Sequence[Event]
) -> None:
    """Write a whole event prefix through the real store, the same path
    production writes through: genesis via `MatchRepository.create`, every
    later event via `TransactionContext.append`."""
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
    """Recover the state a truncated event prefix implies, the same way
    recovery does: fold onto `create_initial_state` seeded from genesis."""
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


async def _persist_two_judged_duels(
    sessions: async_sessionmaker[AsyncSession],
) -> Recorded:
    """Two duels, both judged: the first resolved by timeout, the second
    left running with its own pair of judgements.

    This is what the duel-boundary tests need and `build_rich_stream` does
    not give directly — its loop runs a two-player match all the way to a
    win, and only its hand-played first duel carries judgements at all.
    Rather than extend that loop (which would also force
    `test_the_stream_has_the_expected_shape` to be rewritten for a shape no
    other test needs), this drives `decide`/`fold` by hand for exactly the
    two duels the boundary tests care about, then persists the whole thing
    through `MatchRepository.create` and `TransactionContext.append` — the
    same path production writes through.

    What the domain taught building this: a duel resolved by `ExpireTimer`
    does not, by itself, eliminate anyone. Losing one group out of six
    leaves the loser with five, so `_resolve` returns only `DuelResolved` —
    no `PlayerEliminated`, no `MatchWon` — and turn order simply advances to
    the winner, whose next `DeclareAttack` starts the second duel. That is
    exactly the "resolved duel in between" shape
    `test_the_journal_survives_a_resolved_duel_in_between` needs: `DuelResolved`,
    `AttackDeclared` and `DuelStarted`, in that order, with nothing else
    between the two judged duels.
    """
    board = BoardSize(3, 4)
    settings = MatchSettings()
    match_id = MatchId(uuid4())
    state = create_initial_state(match_id, board, settings)
    events: list[Event] = []
    now = _BASE_TIME

    def apply(command: Command, *, at: datetime, **ctx_kwargs: object) -> None:
        nonlocal state
        ctx = DecisionContext(now=at, **ctx_kwargs)  # type: ignore[arg-type]
        produced = decide(state, command, ctx)
        events.extend(produced)
        state = fold(state, produced)

    players = (PlayerId(uuid4()), PlayerId(uuid4()))
    apply(CreateMatch(board=board, settings=settings, player_count=2), at=now)
    for index, player_id in enumerate(players):
        apply(
            AddPlayer(player_id=player_id, name=f"Игрок {index + 1}", colour="#000000"), at=now
        )
        apply(AssignSecret(player_id=player_id, category=CategoryId(uuid4())), at=now)
    apply(DealBoard(), at=now, deal=make_deal(board, players, dict(state.secrets)))
    apply(StartMatch(), at=now)

    def declare_and_start() -> None:
        nonlocal now
        attacker = state.current_player()
        attacking = next(
            group for group in state.groups_of(attacker) if legal_targets(state, group.id)
        )
        defending = sorted(legal_targets(state, attacking.id))[0]
        apply(
            DeclareAttack(attacking_group=attacking.id, defending_group=defending),
            at=now,
            image_order=tuple(ImageId(uuid4()) for _ in range(IMAGES_PER_DUEL)),
        )
        apply(StartDuel(), at=now)

    def expire() -> None:
        nonlocal now
        duel = state.duel
        assert duel is not None and duel.anchor is not None
        deadline = duel.anchor + timedelta(milliseconds=duel.budgets.get(duel.answering))
        now = deadline + timedelta(seconds=1)
        apply(ExpireTimer(deadline_id=0), at=now)

    # Duel one: two judgements, then let the clock resolve it.
    declare_and_start()
    now += timedelta(seconds=4)
    apply(JudgeCorrect(), at=now)
    now += timedelta(seconds=3)
    apply(JudgePass(), at=now)
    expire()
    assert state.duel is None, "duel one must have resolved before duel two starts"

    # Duel two: two more judgements, left running -- no undo issued here.
    declare_and_start()
    now += timedelta(seconds=4)
    apply(JudgeCorrect(), at=now)
    now += timedelta(seconds=3)
    apply(JudgePass(), at=now)
    assert state.duel is not None, "duel two must still be running"

    await _persist(sessions, match_id, events)
    return Recorded(state=state, events=tuple(events))


async def test_every_context_carries_the_clock_reading(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The domain never reads a clock. This is the only place `now` enters."""
    materialiser = _materialiser(sessions)
    state = build_rich_stream().state
    ctx = await materialiser.build(state, PauseDuel(), _tx())
    assert ctx.now == NOW


async def test_a_plain_command_gets_nothing_it_does_not_need(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A context carrying a deal for a `PauseDuel` would mean the deal was
    drawn — and content drawn under a transaction's locks is not free."""
    ctx = await _materialiser(sessions).build(build_rich_stream().state, PauseDuel(), _tx())
    assert ctx.deal is None
    assert ctx.image_order is None
    assert ctx.duel_journal == ()


async def test_the_journal_holds_one_entry_per_judgement_of_this_duel(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """One `AnswerAccepted` and one `PassUsed` were judged in the rich
    stream's first duel before the undo, so the journal handed to that undo
    must hold exactly those two, oldest first."""
    recorded = build_rich_stream()
    # Everything up to (but not including) the undo: both judgements of the
    # first duel, plus the pause/resume between them, land in the prefix.
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, JudgementUndone))
    state = await _persisted_prefix(sessions, recorded, cut)
    truncated = recorded.events[:cut]

    journal = (
        await _materialiser(sessions).build(state, UndoLastJudgement(), _tx())
    ).duel_journal

    attack = next(e for e in truncated if isinstance(e, AttackDeclared))
    assert len(journal) == 2, f"expected exactly the two judged entries, got {journal}"
    first, second = journal
    assert first.seq < second.seq, "the journal must be oldest first"
    assert first.answering == attack.attacker, "the attacker answers the first image"
    assert first.image_index == 0
    assert second.answering == attack.defender, "AnswerAccepted hands the turn to the defender"
    assert second.image_index == 1


async def test_a_journal_entrys_seq_names_the_judgement_it_records(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """`_undo` copies `entry.seq` verbatim into `JudgementUndone.undone_seq`,
    an append-only field. It must name the judging event itself -- its
    actual position in the persisted log -- not the event immediately
    before it. Every other journal assertion in this module is relative
    (`>=` a boundary, `<` a count), so none of them can see an absolute
    off-by-one here; this one reads the real persisted seq of each judging
    event and checks the journal against it directly."""
    recorded = build_rich_stream()
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, JudgementUndone))
    state = await _persisted_prefix(sessions, recorded, cut)

    journal = (
        await _materialiser(sessions).build(state, UndoLastJudgement(), _tx())
    ).duel_journal

    # seq is one-based over the persisted prefix, in the same order it was
    # written: MatchRepository.create gives the first event seq 1, and each
    # later TransactionContext.append call assigns the next seq in turn.
    judging_seqs = [
        index + 1
        for index, event in enumerate(recorded.events[:cut])
        if isinstance(event, AnswerAccepted | PassUsed)
    ]
    assert [entry.seq for entry in journal] == judging_seqs


async def test_the_journal_never_reaches_into_the_previous_duel(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The carry-forward from plan 1, and the reason this task exists.

    A stream with judgements in duel one and judgements in duel two must
    hand duel two's undo only duel two's entries. If this fails, an
    operator's undo can reach back across a resolved duel and rewrite an
    ownership transfer the rules call final.

    The assertion is on `seq`, not on counts, precisely because a
    count-based assertion would pass if the journal held one entry from each
    duel.
    """
    recorded = await _persist_two_judged_duels(sessions)
    materialiser = _materialiser(sessions)

    journal = (
        await materialiser.build(recorded.state, UndoLastJudgement(), _tx())
    ).duel_journal

    last_duel_started = max(
        index for index, event in enumerate(recorded.events)
        if isinstance(event, DuelStarted)
    )
    boundary = last_duel_started + 1  # seq is one-based over the same list
    assert journal, "the second duel had judgements; the journal cannot be empty"
    assert all(entry.seq >= boundary for entry in journal), (
        "an entry from before this duel started would let an undo rewrite a "
        f"resolved duel: {[entry.seq for entry in journal]} against {boundary}"
    )


async def test_the_journal_survives_a_resolved_duel_in_between(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The boundary is `DuelStarted`, not "the last few events".

    A duel that resolved between the two judged ones puts `DuelResolved`,
    `AttackDeclared` and `DuelStarted` in the log in that order. A journal
    built by scanning backwards for judgements without stopping at
    `DuelStarted` would sail straight through all three.
    """
    recorded = await _persist_two_judged_duels(sessions)
    journal = (
        await _materialiser(sessions).build(recorded.state, UndoLastJudgement(), _tx())
    ).duel_journal
    judgements_in_the_log = sum(
        1 for event in recorded.events if isinstance(event, AnswerAccepted | PassUsed)
    )
    assert len(journal) < judgements_in_the_log, (
        "the journal holds every judgement in the match, so the duel "
        "boundary is not being honoured at all"
    )


async def test_an_undone_judgement_leaves_the_journal(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A `JudgementUndone` in the log means its entry was already consumed.
    Leaving it would let two undos rewind one judgement — the chain would
    stop being a chain."""
    recorded = build_rich_stream()
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, JudgementUndone)) + 1
    state = await _persisted_prefix(sessions, recorded, cut)
    truncated = recorded.events[:cut]

    journal = (
        await _materialiser(sessions).build(state, UndoLastJudgement(), _tx())
    ).duel_journal

    attack = next(e for e in truncated if isinstance(e, AttackDeclared))
    assert len(journal) == 1, "the undone PassUsed entry must not remain in the journal"
    assert journal[0].answering == attack.attacker
    assert journal[0].image_index == 0


async def test_the_journal_is_empty_for_a_duel_nobody_has_judged(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The domain rejects `UndoLastJudgement` with NOTHING_TO_UNDO when the
    journal is empty. That rejection is only correct if the emptiness is."""
    recorded = build_rich_stream()
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, DuelStarted)) + 1
    state = await _persisted_prefix(sessions, recorded, cut)
    duel = state.duel
    assert duel is not None and duel.anchor is not None

    # A clock reading a day after the duel started (as the module-wide `NOW`
    # is here) would itself expire the duel and make `decide` resolve it
    # rather than reject it — a different code path than the one this test
    # means to exercise. Read the clock a second after the real anchor
    # instead, comfortably inside the duel's budget.
    materialiser = Materialiser(
        FakeClock(duel.anchor + timedelta(seconds=1)),
        MatchRepository(sessions),
        FakeCategoryBank(),
        Random(0),
    )
    ctx = await materialiser.build(state, UndoLastJudgement(), _tx())
    assert ctx.duel_journal == ()

    with pytest.raises(Rejected) as excinfo:
        decide(state, UndoLastJudgement(), ctx)
    assert excinfo.value.reason is RejectionReason.NOTHING_TO_UNDO


def _setup_state_before(recorded: Recorded, event_type: type) -> tuple[MatchState, Sequence[Event]]:
    cut = next(i for i, e in enumerate(recorded.events) if isinstance(e, event_type))
    truncated = recorded.events[:cut]
    return _fold_prefix(recorded.state.id, truncated), truncated


async def test_a_declared_attack_draws_a_pack_for_the_defenders_category(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The duel is played on the defender's category (§2.6), and the draw
    happens at declaration, not at start — otherwise the warm-up window is
    fictional and the stage screen gets its preload and its live deadline
    in the same frame (§3.5)."""
    recorded = build_rich_stream()
    state, _ = _setup_state_before(recorded, AttackDeclared)
    assert state.status is MatchStatus.RUNNING and state.duel is None

    attacker = state.current_player()
    attacking = next(
        group for group in state.groups_of(attacker) if legal_targets(state, group.id)
    )
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    defending = state.groups[defending_id]

    bank = FakeCategoryBank()
    materialiser = Materialiser(FakeClock(NOW), MatchRepository(sessions), bank, Random(0))
    ctx = await materialiser.build(
        state,
        DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
        _tx(),
    )

    assert ctx.image_order is not None
    assert len(ctx.image_order) == IMAGE_PACK_SIZE
    assert bank.drawn_packs == [(defending.category, IMAGE_PACK_SIZE)], (
        "the pack must be drawn for the defender's category, at declaration"
    )


async def test_the_image_pack_is_not_redrawn_at_start_duel(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§3.5 names this by name -- the exact regression the design's donor
    project shipped. The pack is drawn whole at declaration; `StartDuel`
    must not touch the bank a second time."""
    recorded = build_rich_stream()
    state, _ = _setup_state_before(recorded, AttackDeclared)
    attacker = state.current_player()
    attacking = next(
        group for group in state.groups_of(attacker) if legal_targets(state, group.id)
    )
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    defending = state.groups[defending_id]

    bank = FakeCategoryBank()
    materialiser = Materialiser(FakeClock(NOW), MatchRepository(sessions), bank, Random(0))

    declare = DeclareAttack(attacking_group=attacking.id, defending_group=defending_id)
    ctx = await materialiser.build(state, declare, _tx())
    state = fold(state, decide(state, declare, ctx))
    assert bank.drawn_packs == [(defending.category, IMAGE_PACK_SIZE)], (
        "the test needs exactly one draw from declaration before StartDuel runs"
    )

    ctx = await materialiser.build(state, StartDuel(), _tx())

    assert ctx.image_order is None, "StartDuel must not carry its own image order"
    assert bank.drawn_packs == [(defending.category, IMAGE_PACK_SIZE)], (
        "the pack must not be redrawn at StartDuel"
    )


async def _deal_state_and_plan(
    sessions: async_sessionmaker[AsyncSession], recorded: Recorded
) -> tuple[MatchState, DealPlan]:
    state, _ = _setup_state_before(recorded, BoardDealt)
    ctx = await _materialiser(sessions).build(state, DealBoard(), _tx())
    assert ctx.deal is not None
    return state, ctx.deal


async def test_a_deal_gives_every_player_an_equal_share(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    state, deal = await _deal_state_and_plan(sessions, recorded)
    assert len(deal.cells) == state.board.cell_count
    counts = Counter(dealt.owner for dealt in deal.cells)
    assert set(counts) == {p.id for p in state.players}
    expected_share = state.board.cell_count // state.player_count
    assert set(counts.values()) == {expected_share}, (
        f"deal is not split evenly: {counts}"
    )


async def test_a_deal_puts_each_secret_on_a_cell_its_owner_holds(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    state, deal = await _deal_state_and_plan(sessions, recorded)
    by_category = {dealt.category: dealt for dealt in deal.cells}
    for player_id, secret in state.secrets.items():
        placed = by_category[secret]
        assert placed.owner == player_id, (
            f"secret {secret} landed on {placed.owner}'s cell, not {player_id}'s"
        )
        assert not placed.revealed, "a secret's cell must not start revealed"


async def test_a_deal_gives_every_cell_a_category_of_its_own(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    recorded = build_rich_stream()
    _, deal = await _deal_state_and_plan(sessions, recorded)
    categories = [dealt.category for dealt in deal.cells]
    assert len(set(categories)) == len(categories), "two cells share a category"


async def test_the_deal_the_materialiser_builds_is_one_the_domain_accepts(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The domain validates a deal ten different ways. Rather than assert
    those ten properties again here, hand the plan to `decide` and let the
    real validator judge it — the one that will judge it in production."""
    recorded = build_rich_stream()
    state, _ = _setup_state_before(recorded, BoardDealt)
    ctx = await _materialiser(sessions).build(state, DealBoard(), _tx())

    produced = decide(state, DealBoard(), ctx)

    assert len(produced) == 1
    dealt = produced[0]
    assert isinstance(dealt, BoardDealt)


async def test_a_library_too_small_to_deal_is_a_rejection_not_a_crash(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.3: «нехватка контента при отборе — обычный отказ, не авария»."""
    recorded = build_rich_stream()
    state, _ = _setup_state_before(recorded, BoardDealt)
    bank = FakeCategoryBank(exhaust_after=1)
    materialiser = Materialiser(FakeClock(NOW), MatchRepository(sessions), bank, Random(0))

    with pytest.raises(ContentExhausted):
        await materialiser.build(state, DealBoard(), _tx())


async def test_two_deals_from_the_same_state_differ(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """«Раздача случайна и вразброс» (§3.4), and re-dealing is the host's
    shuffle button. A deterministic deal would make that button a no-op."""
    recorded = build_rich_stream()
    state, _ = _setup_state_before(recorded, BoardDealt)
    materialiser = Materialiser(
        FakeClock(NOW), MatchRepository(sessions), FakeCategoryBank(), Random(0)
    )

    first = (await materialiser.build(state, DealBoard(), _tx())).deal
    second = (await materialiser.build(state, DealBoard(), _tx())).deal
    assert first is not None and second is not None

    def layout(plan: DealPlan) -> tuple[tuple[object, object], ...]:
        return tuple(sorted((dealt.cell, dealt.owner) for dealt in plan.cells))

    assert layout(first) != layout(second), (
        "re-dealing from the same state must reshuffle, not repeat the same layout"
    )
