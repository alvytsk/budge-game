"""Reading and creating whole matches.

`create` exists as its own operation because genesis is the one append with
nothing to be optimistic about: `append` guards on a `matches` row that does
not exist yet. The match row and the seq-1 event are written in one
transaction, and every later event goes through `TransactionContext.append`.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.codec import encode
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.ids import MatchId
from podvinsya.domain.state import MatchStatus


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
