"""One attempt at landing one command in the log, retried when that is
provably safe and reconciled when it is not.

§6.3's failure-policy table, made executable: every row corresponds to
exactly one branch below, and which branch a failure takes is decided by
what the driver's own exception says, not by matching text against it.

There is one outcome union here, not two: `run` returns exactly
`origins.CommandOutcome` — `Accepted`, `NoOp`, `Rejected` or `Failed` — the
same four Task 2 already defined. An earlier draft of this module minted
its own `Committed`/`NothingToDo`/`Refused`/`Broken` beside them, four
pairs with identical shapes and different names, plus a translation that
could only ever be a bug factory. `_Retry` is this module's own sentinel
and never escapes `run`.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from random import Random
from typing import assert_never

from sqlalchemy.exc import DBAPIError

from podvinsya.db.errors import ConcurrentModification
from podvinsya.domain.decide import decide
from podvinsya.domain.errors import Rejected as DomainRejected
from podvinsya.domain.events import Event
from podvinsya.domain.state import MatchState
from podvinsya.runtime.materialiser import Materialiser
from podvinsya.runtime.origins import (
    Accepted,
    CommandOutcome,
    Failed,
    NoOp,
    QueuedCommand,
    Rejected,
)
from podvinsya.services.ports import (
    Clock,
    ContentExhausted,
    Reconciliation,
    RuntimeCode,
    UnitOfWorkPort,
)

logger = logging.getLogger(__name__)

RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})  # serialisation failure, deadlock


def _sqlstate(error: DBAPIError) -> str | None:
    """Read the SQLSTATE off the driver's own exception.

    Matching on message text would work today and stop working silently on
    the next driver upgrade, which is exactly the kind of breakage nobody
    notices until a retry storm. asyncpg's own exceptions populate
    `.sqlstate` — confirmed against the installed driver, not assumed —
    and have no `.pgcode` at all.
    """
    return getattr(error.orig, "sqlstate", None)


@dataclass(frozen=True, slots=True)
class _Retry:
    """Private to this module. `run` loops on it and never returns it."""


class CommitPath:
    def __init__(
        self,
        uow: UnitOfWorkPort,
        materialiser: Materialiser,
        clock: Clock,
        random: Random,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._uow = uow
        self._materialiser = materialiser
        self._clock = clock
        self._random = random
        self._max_attempts = max_attempts
        self._base_delay_ms = 50

    async def run(self, state: MatchState, queued: QueuedCommand) -> CommandOutcome:
        for attempt in range(self._max_attempts):
            outcome = await self._attempt(state, queued)
            if not isinstance(outcome, _Retry):
                return outcome
            if attempt + 1 < self._max_attempts:
                await self._backoff(attempt)
        return Failed(RuntimeCode.DATABASE_UNAVAILABLE, "retries exhausted")

    async def _attempt(
        self, state: MatchState, queued: QueuedCommand
    ) -> CommandOutcome | _Retry:
        events: tuple[Event, ...] = ()
        body_completed = False
        try:
            async with self._uow.begin() as tx:
                ctx = await self._materialiser.build(state, queued.command, tx)
                events = decide(state, queued.command, ctx)
                if not events:
                    return NoOp()
                await tx.append(
                    state.id,
                    expected_last_seq=state.seq,
                    events=events,
                    operation_id=queued.operation_id,
                )
                body_completed = True
        except DomainRejected as refusal:
            return Rejected(refusal.reason)
        except ContentExhausted as shortfall:
            return Failed(RuntimeCode.CONTENT_UNAVAILABLE, str(shortfall))
        except ConcurrentModification:
            # §6.3 gives this no retry row: a retry would append events
            # decided against state that is no longer current.
            return Failed(RuntimeCode.INTERNAL, "another writer advanced this match")
        except DBAPIError as error:
            return await self._after_database_error(error, state, queued, events, body_completed)
        except Exception as unexpected:
            # decide or the materialiser blew up. §6.3 gives both the same
            # answer and explicitly no retries: a bug reproduces exactly on
            # replay, so retrying it three times just delays the diagnosis.
            logger.exception("attempt failed for %s", state.id)
            return Failed(RuntimeCode.INTERNAL, repr(unexpected))
        return Accepted(events)

    async def _after_database_error(
        self,
        error: DBAPIError,
        state: MatchState,
        queued: QueuedCommand,
        events: tuple[Event, ...],
        body_completed: bool,
    ) -> CommandOutcome | _Retry:
        """A body that never finished rolled back, and the failure is
        unambiguous — retry it if the SQLSTATE says so. A body that
        finished means the COMMIT was already in flight, and whether it
        landed is exactly what reconciliation answers.
        """
        if not body_completed:
            if _sqlstate(error) in RETRYABLE_SQLSTATES:
                return _Retry()
            return Failed(RuntimeCode.DATABASE_UNAVAILABLE, repr(error))

        outcome = await self._uow.reconcile(
            state.id,
            queued.operation_id,
            expected_last_seq=state.seq,
            events=events,
        )
        match outcome:
            case Reconciliation.MATCHED:
                return Accepted(events)
            case Reconciliation.ABSENT:
                return _Retry()
            case Reconciliation.DIVERGED:
                return Failed(RuntimeCode.INTERNAL, "the log diverged from this batch")
            case _:  # pragma: no cover - exhaustiveness guard
                assert_never(outcome)

    async def _backoff(self, attempt: int) -> None:
        delay_ms = self._base_delay_ms * (2**attempt)
        jitter = self._random.uniform(0.5, 1.5)
        await self._clock.sleep_until(
            self._clock.now() + timedelta(milliseconds=delay_ms * jitter)
        )
