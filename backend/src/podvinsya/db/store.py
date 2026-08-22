"""The event store: one transaction, one optimistic append.

`append`'s signature is the one §6.2 writes out verbatim, so the runtime's
cycle can be transcribed from the spec without adaptation.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.db.codec import encode
from podvinsya.db.codec.registry import WIRE_NAMES
from podvinsya.db.errors import ConcurrentModification
from podvinsya.db.models import Match, MatchEventRow
from podvinsya.domain.events import Event
from podvinsya.domain.ids import MatchId


class TransactionContext:
    """Everything the runtime may do inside one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None:
        """Write one command's events, or none of them.

        The UPDATE goes first, before any INSERT: it takes the match row's
        lock, so a competing appender blocks there rather than racing toward
        the same `seq`. Matching zero rows means someone else already moved
        `last_seq`, which is a `ConcurrentModification`, not a retry.

        `operation_id` arrives from the caller and is never invented here —
        §5.1 makes generating it the server's job, in the runtime.
        """
        if not events:
            raise ValueError(
                "append needs at least one event: §6.2 resolves a no-op without "
                "opening the log at all"
            )
        # `execute()` on an UPDATE always returns a CursorResult at runtime,
        # but the ORM session's overloads type it as the base `Result[Any]`,
        # which has no `rowcount`. The cast makes the runtime type explicit.
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Match)
                .where(Match.id == match_id, Match.last_seq == expected_last_seq)
                .values(last_seq=expected_last_seq + len(events))
            ),
        )
        if result.rowcount != 1:
            raise ConcurrentModification(match_id, expected_last_seq)
        for offset, event in enumerate(events, start=1):
            wire_type, schema_version, payload = encode(event)
            self.session.add(
                MatchEventRow(
                    match_id=match_id,
                    seq=expected_last_seq + offset,
                    operation_id=operation_id,
                    type=wire_type,
                    schema_version=schema_version,
                    payload=payload,
                )
            )
        await self.session.flush()


class Reconciliation(StrEnum):
    """What the log says about a batch whose commit outcome is unknown."""

    MATCHED = "matched"
    """The batch is durable. Fold it into memory and carry on."""

    ABSENT = "absent"
    """Nothing carries this operation_id. The commit did not land; retry."""

    DIVERGED = "diverged"
    """Something else is there. Quarantine — there is no "almost matched"."""


class UnitOfWork:
    """Opens transactions. Nothing outside a `begin()` block writes."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[TransactionContext]:
        async with self._sessions() as session, session.begin():
            yield TransactionContext(session)

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation:
        """Compare the batch itself against what the log holds.

        Not "does a row with this operation_id exist" — the exact seq range,
        the row count, and the ordered wire types, all three (§6.3). A
        partially applied batch, a batch at the wrong position, or a batch
        of different events is a divergence, and divergence is a quarantine.
        """
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(MatchEventRow.seq, MatchEventRow.type)
                    .where(
                        MatchEventRow.match_id == match_id,
                        MatchEventRow.operation_id == operation_id,
                    )
                    .order_by(MatchEventRow.seq)
                )
            ).all()
        if not rows:
            return Reconciliation.ABSENT
        expected_seqs = list(range(expected_last_seq + 1, expected_last_seq + 1 + len(events)))
        if [row.seq for row in rows] != expected_seqs:
            return Reconciliation.DIVERGED
        if [row.type for row in rows] != [WIRE_NAMES[type(event)] for event in events]:
            return Reconciliation.DIVERGED
        return Reconciliation.MATCHED
