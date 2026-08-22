"""Every non-deterministic input the domain needs, resolved into a value.

`decide` is pure: it reads no clock, draws no random number, and queries
nothing. That is what makes it testable, and it holds only because this
module does all of it first and hands the results over as data.

Three of the four inputs are drawn only for the command that needs them. A
context carrying a deal for a `PauseDuel` would mean content was selected —
under transaction locks — for a command that will never look at it.
"""

import logging
from datetime import datetime
from random import Random
from uuid import uuid4

from podvinsya.domain.actions import Command, DealBoard, DeclareAttack, UndoLastJudgement
from podvinsya.domain.context import DealPlan, DealtCell, DecisionContext, JournalEntry
from podvinsya.domain.events import (
    AnswerAccepted,
    DuelStarted,
    JudgementUndone,
    PassUsed,
)
from podvinsya.domain.evolve import evolve
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import GroupId, ImageId
from podvinsya.domain.state import MatchState
from podvinsya.services.ports import (
    CategoryBank,
    Clock,
    MatchRepositoryPort,
    Transaction,
)

logger = logging.getLogger(__name__)

# One pack is drawn per duel and never extended (§3.5). A duel is bounded by
# the two budgets — sixty seconds plus at most fifteen of bonus each — and a
# host cannot judge an image in much under a second of speech, so sixty is
# comfortably more than a duel can consume. Running past the end is a content
# defect, not a domain transition (§8), and it surfaces as a rejection.
IMAGE_PACK_SIZE = 60


class Materialiser:
    def __init__(
        self,
        clock: Clock,
        repository: MatchRepositoryPort,
        bank: CategoryBank,
        random: Random,
    ) -> None:
        self._clock = clock
        self._repository = repository
        self._bank = bank
        self._random = random

    async def build(
        self,
        state: MatchState,
        command: Command,
        tx: Transaction,
        *,
        at: datetime | None = None,
    ) -> DecisionContext:
        """`at` overrides the clock reading and exists for exactly one
        caller: recovery (§4.4), which pauses a duel as of its own anchor so
        the outage is charged to nobody. Everywhere else the clock decides,
        and passing `at` from anywhere else is a bug worth reviewing."""
        now = at if at is not None else self._clock.now()
        match command:
            case DealBoard():
                return DecisionContext(now=now, deal=await self._deal(state, tx))
            case DeclareAttack():
                return DecisionContext(
                    now=now, image_order=await self._images(state, command, tx)
                )
            case UndoLastJudgement():
                return DecisionContext(now=now, duel_journal=await self._journal(state))
            case _:
                return DecisionContext(now=now)

    async def _journal(self, state: MatchState) -> tuple[JournalEntry, ...]:
        """Rebuild the current duel's judging history from the log.

        The domain reads this and trusts it, so the rule that undo cannot
        reach past the start of the current duel lives here: `DuelStarted`
        clears the journal. Without that line, an undo could rewind a
        judgement from a duel that has already resolved and transferred
        ownership — a transfer §3.7 calls final.

        A `JudgementUndone` in the log pops the entry it consumed, so a
        chain of undos walks backwards one judgement at a time instead of
        rewinding the same one twice.

        Each entry's `seq` names the judging event itself, not the event
        before it: `_undo` copies it verbatim into `JudgementUndone.undone_seq`,
        an append-only field, so getting this wrong here would mean every
        undo this system ever records names the wrong event, permanently.

        The whole log is replayed rather than kept in memory: a match is a
        few dozen events, this runs only for `UndoLastJudgement`, and a
        cached journal would be one more thing that can disagree with the
        log after a recovery.
        """
        events = await self._repository.read_events(state.id)
        replay = create_initial_state(state.id, state.board, state.settings)
        journal: list[JournalEntry] = []
        for event in events:
            match event:
                case DuelStarted():
                    journal.clear()
                case AnswerAccepted() | PassUsed():
                    journal.append(_snapshot(replay))
                case JudgementUndone():
                    if journal:
                        journal.pop()
                case _:
                    pass
            replay = evolve(replay, event)
        return tuple(journal)

    async def _images(
        self, state: MatchState, command: DeclareAttack, tx: Transaction
    ) -> tuple[ImageId, ...]:
        """Draw the whole pack at declaration (§3.5).

        The defender's category is the one played (§2.6), and the order is
        settled once and written into the event whole, so the stage screen
        can preload every image while the host explains the category.
        """
        defending = state.groups[command.defending_group]
        return await self._bank.draw_images(tx, defending.category, IMAGE_PACK_SIZE)

    async def _deal(self, state: MatchState, tx: Transaction) -> DealPlan:
        """§3.4, in order: cells split evenly and at random, a distinct
        category on every cell, each player's secret on one of their own
        cells, and the ordinary categories drawn from the active library
        without repeats.

        A re-deal is the host's shuffle button, so this must genuinely
        differ from run to run — the randomness is injected rather than
        global so a test can pin it without the production path being
        deterministic.
        """
        players = tuple(player.id for player in state.players)
        secrets = dict(state.secrets)
        cells = list(state.board.cells())
        self._random.shuffle(cells)

        per_player = len(cells) // len(players)
        ordinary = await self._bank.draw_categories(
            tx, len(cells) - len(players), exclude=frozenset(secrets.values())
        )

        dealt: list[DealtCell] = []
        pool = iter(ordinary)
        for index, owner in enumerate(players):
            owned = cells[index * per_player : (index + 1) * per_player]
            for position, cell in enumerate(owned):
                is_secret = position == 0  # the shuffle already made this random
                dealt.append(
                    DealtCell(
                        cell=cell,
                        owner=owner,
                        category=secrets[owner] if is_secret else next(pool),
                        group_id=GroupId(uuid4()),
                        revealed=not is_secret,
                    )
                )
        return DealPlan(cells=tuple(dealt))


def _snapshot(state: MatchState) -> JournalEntry:
    """The duel as it stood immediately before one judging event, tagged
    with that judging event's own seq.

    `state` is the replay *before* folding the judging event, so
    `state.seq` is the seq of whatever came before it. `evolve` assigns
    seq by incrementing once per event and genesis persists at seq 1, so
    the judging event about to be folded lands at `state.seq + 1` — that is
    the value undo must report, since it is what actually gets undone, not
    the event that merely preceded it.
    """
    duel = state.duel
    assert duel is not None, "a judging event outside a duel is a corrupt log"
    return JournalEntry(
        seq=state.seq + 1,
        budgets=duel.budgets,
        answering=duel.answering,
        image_index=duel.index,
    )
