"""Everything between a route and the runtime, and nothing else.

Ruling 5 splits the transports but not the path: `CreateMatch` arrives over
REST and `JudgeCorrect` over a socket, and both end up here. That is what
makes the outcome mapping in `outcomes.py` a single behaviour rather than
one per transport.

`api/` never opens a transaction to write. `MatchLifecycle.create` is the
one apparent exception and is not one: genesis is the single append with
nothing to be optimistic about — `TransactionContext.append` guards on a
`matches` row that does not exist yet — so plan 2 gave it its own
repository method, and this calls that.
"""

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.api.content import CachingContentDirectory
from podvinsya.db.errors import MatchNotFound
from podvinsya.db.models import Match, MatchPlayer
from podvinsya.domain.actions import Command, CreateMatch
from podvinsya.domain.board import BoardSize
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.errors import Rejected as DomainRejected
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState
from podvinsya.runtime.errors import MatchAlreadyRunning
from podvinsya.runtime.manager import MatchManager
from podvinsya.runtime.origins import Accepted, CommandOutcome, Rejected
from podvinsya.services.ports import Clock, MatchRepositoryPort

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MatchSummary:
    """One row of §5.2's read model, as `GET /api/matches` returns it."""

    id: MatchId
    status: str
    winner_id: str | None
    last_seq: int
    players: tuple[tuple[str, str, bool], ...]


class MatchLifecycle:
    """The two things that are not `manager.submit`: creating a match, and
    making sure one is live before a command is sent to it."""

    def __init__(
        self,
        repository: MatchRepositoryPort,
        manager: MatchManager,
        clock: Clock,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._repository = repository
        self._manager = manager
        self._clock = clock
        self._sessions = sessions
        self._start_lock = asyncio.Lock()

    async def create(
        self, board: BoardSize, settings: MatchSettings, player_count: int
    ) -> tuple[MatchId, CommandOutcome]:
        """Decide genesis, then write it — in that order, always.

        Ruling 4: the API re-validates nothing. An invalid board raises
        `Rejected` out of `decide` and this returns it *before* the
        repository is touched, so a refused creation leaves no row and no
        event behind.
        """
        match_id = MatchId(uuid4())
        state = create_initial_state(match_id, board, settings)
        command = CreateMatch(board=board, settings=settings, player_count=player_count)
        try:
            events = decide(state, command, DecisionContext(now=self._clock.now()))
        except DomainRejected as rejected:
            return match_id, Rejected(rejected.reason)

        genesis = events[0]
        assert isinstance(genesis, MatchCreated), "decide(CreateMatch) emits MatchCreated first"
        await self._repository.create(match_id, genesis, operation_id=str(uuid4()))
        return match_id, Accepted(events)

    async def ensure_live(self, match_id: MatchId) -> None:
        """Start `match_id`'s runtime unless it is already running.

        `MatchAlreadyRunning` is swallowed rather than raised: two sockets
        opening at once — the operator's console and the stage screen, which
        is exactly how a show begins — is normal, and turning the second one
        into a 500 would make the ordinary case an error. The manager's own
        lock makes the recovery itself happen once; the lock here keeps this
        method from *starting* two recoveries that then race to be the one
        that raises.
        """
        if self._manager.runtime_for(match_id) is not None:
            return
        async with self._start_lock:
            if self._manager.runtime_for(match_id) is not None:
                return
            try:
                await self._manager.start(match_id)
            except MatchAlreadyRunning:  # pragma: no cover - the guards above cover it
                logger.debug("%s was started concurrently; using the running one", match_id)

    async def state_of(self, match_id: MatchId) -> MatchState:
        """The live state, recovering the match first if it is not running.

        Read off the runtime rather than folded here: the runtime's copy is
        the one every command is decided against, and a snapshot folded
        separately could disagree with it across a command in flight.
        """
        await self.ensure_live(match_id)
        runtime = self._manager.runtime_for(match_id)
        if runtime is None:  # pragma: no cover - ensure_live either starts it or raises
            raise MatchNotFound(match_id)
        return runtime.state

    async def summaries(self) -> tuple[MatchSummary, ...]:
        """Ruling 14: §5.2's projection tables, finally read by something.

        A list endpoint that folded every log instead would make the read
        model dead code with tests — and would read every event of every
        match ever played to render one admin screen.
        """
        async with self._sessions() as session:
            matches = (
                (await session.execute(select(Match).order_by(Match.created_at.desc())))
                .scalars()
                .all()
            )
            players = (
                (await session.execute(select(MatchPlayer))).scalars().all()
            )
        by_match: dict[MatchId, list[tuple[str, str, bool]]] = {}
        for player in players:
            by_match.setdefault(MatchId(player.match_id), []).append(
                (player.name, player.colour, player.eliminated)
            )
        return tuple(
            MatchSummary(
                id=MatchId(match.id),
                status=match.status,
                winner_id=str(match.winner_id) if match.winner_id is not None else None,
                last_seq=match.last_seq,
                players=tuple(by_match.get(MatchId(match.id), ())),
            )
            for match in matches
        )


class CommandGateway:
    """The one door every command goes through, whatever transport it came
    in on."""

    def __init__(self, lifecycle: MatchLifecycle, manager: MatchManager) -> None:
        self._lifecycle = lifecycle
        self._manager = manager

    async def submit(self, match_id: MatchId, command: Command) -> CommandOutcome:
        """Hand the command to the match's queue and return what the loop
        decided — verbatim, never a summary of it.

        `ensure_live` first: `MatchManager.submit` raises `KeyError` for a
        match nobody has started, and "first touch starts the runtime" is
        the manager's documented contract for its caller, which is this.
        """
        await self._lifecycle.ensure_live(match_id)
        return await self._manager.submit(match_id, command)


@dataclass(frozen=True, slots=True)
class Services:
    """What `build_app` puts on `app.state` and every route reads back."""

    lifecycle: MatchLifecycle
    gateway: CommandGateway
    manager: MatchManager
    directory: CachingContentDirectory
    clock: Clock
