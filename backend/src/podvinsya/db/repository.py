"""Reading and creating whole matches.

`create` exists as its own operation because genesis is the one append with
nothing to be optimistic about: `append` guards on a `matches` row that does
not exist yet. The match row and the seq-1 event are written in one
transaction, and every later event goes through `TransactionContext.append`.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.codec import decode, encode
from podvinsya.db.errors import EventStreamCorrupt, MatchNotFound
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.evolve import fold
from podvinsya.domain.genesis import create_initial_state
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchState, MatchStatus


@dataclass(frozen=True, slots=True)
class LoadedMatch:
    """A folded match and the log position it was folded from."""

    state: MatchState
    last_seq: int


class MatchRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self, match_id: MatchId, event: MatchCreated, *, operation_id: str
    ) -> None:
        """Write the genesis event and the row the read model hangs off.

        The caller has already run `decide(CreateMatch)`; this layer never
        decides anything.
        """
        wire_type, schema_version, payload = encode(event)
        async with self._sessions() as session, session.begin():
            session.add(Match(id=match_id, status=MatchStatus.SETUP.value, last_seq=1))
            session.add(
                MatchEventRow(
                    match_id=match_id,
                    seq=1,
                    operation_id=operation_id,
                    type=wire_type,
                    schema_version=schema_version,
                    payload=payload,
                )
            )

    async def read_events(self, match_id: MatchId) -> tuple[Event, ...]:
        """Select and decode one match's whole log, in seq order.

        This is the only place that turns a stored log back into events:
        `load` folds what this returns to recover a match's state, and a
        rebuild caller can hand the same tuple to
        `podvinsya.db.projection.rebuild` to recover the read model — which
        is what makes "the read model is rebuilt from the log" something
        the codebase can actually do, not just something the tests assert
        with events they already held in memory.
        """
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(MatchEventRow)
                        .where(MatchEventRow.match_id == match_id)
                        .order_by(MatchEventRow.seq)
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            raise MatchNotFound(match_id)
        if [row.seq for row in rows] != list(range(1, len(rows) + 1)):
            raise EventStreamCorrupt(f"{match_id}: the log has a gap or starts past seq 1")
        return tuple(decode(row.type, row.schema_version, row.payload) for row in rows)

    async def load(self, match_id: MatchId) -> LoadedMatch:
        """Rebuild a match by folding its log, and nothing else.

        The genesis event supplies the board and settings
        `create_initial_state` needs, so they are read from the log rather
        than from the `matches` row: that row is a projection, and a
        projection must never become the thing recovery trusts. Folding
        `MatchCreated` again immediately afterwards is harmless — `evolve`
        assigns the same values it just supplied.

        §4.4's pause-on-recovery is deliberately absent. It emits an event,
        and this layer emits nothing.
        """
        events = await self.read_events(match_id)
        genesis = events[0]
        if not isinstance(genesis, MatchCreated):
            raise EventStreamCorrupt(
                f"{match_id}: the log begins with {type(genesis).__name__}, not MatchCreated"
            )
        state = fold(create_initial_state(match_id, genesis.board, genesis.settings), events)
        return LoadedMatch(state=state, last_seq=state.seq)
