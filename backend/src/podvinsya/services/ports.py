"""Every capability the runtime needs, declared as a Protocol.

There are no implementations under `services/`. That is what keeps the
direction api → services → domain one-way, and what makes a test with a
fake clock, a breaking broadcaster and a breaking commit mechanical rather
than heroic.

Two names are imported from `db/`: `Reconciliation` and `LoadedMatch`. Both
are data — a three-valued enum and a frozen pair — not capability, and no
service code calls into `db` because they are here. See the plan's ruling.
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from podvinsya.db.repository import LoadedMatch as LoadedMatch
from podvinsya.db.store import Reconciliation as Reconciliation
from podvinsya.domain.errors import RejectionReason
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.ids import CategoryId, ImageId, MatchId
from podvinsya.domain.state import MatchState


class Clock(Protocol):
    """`sleep_until`, never `sleep`: no test may wait on wall-clock time,
    and a duration-based API would force a fake clock to reconstruct the
    absolute deadline the runtime has already computed."""

    def now(self) -> datetime: ...
    async def sleep_until(self, when: datetime) -> None: ...


class Broadcaster(Protocol):
    """Synchronous on purpose: the loop is forbidden to wait on a socket
    write, and a `def` cannot be accidentally awaited. It takes domain
    objects because only the WebSocket layer knows each subscriber's
    context, so the projection has to happen there.

    The contract a test enforces, which the signature cannot: project and
    `put_nowait`, nothing else. No await, no blocking I/O, no exception
    escaping.
    """

    def publish(
        self,
        match_id: MatchId,
        base_seq: int,
        state: MatchState,
        events: Sequence[Event],
    ) -> None: ...


class RuntimeCode(StrEnum):
    """Why a command did not succeed for a reason that is not the domain's.

    `CONTENT_UNAVAILABLE` is the odd one and deliberately so: §6.3 calls a
    content shortfall «обычный отказ, не авария», so it reaches the caller
    through this enum but never quarantines the match. The distinction the
    spec draws is about runtime health, not about which method the origin
    is told through.
    """

    QUARANTINED = "quarantined"
    DATABASE_UNAVAILABLE = "database_unavailable"
    CONTENT_UNAVAILABLE = "content_unavailable"
    INTERNAL = "internal"


class Origin(Protocol):
    """Whoever is waiting for a command's outcome.

    Resolution is exactly-once and idempotent, and it never raises. A REST
    client can disconnect while its command is queued, and `set_result` on
    a cancelled future would otherwise raise `InvalidStateError` *after*
    the commit — turning a delivery failure on a dead request into a
    quarantine for a match whose state is durable and correct.
    """

    def resolve_ok(self, events: Sequence[Event]) -> None: ...
    def resolve_noop(self) -> None: ...
    def resolve_rejected(self, reason: RejectionReason) -> None: ...
    def resolve_failed(self, code: RuntimeCode, message: str) -> None: ...


class Transaction(Protocol):
    async def append(
        self,
        match_id: MatchId,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
        operation_id: str,
    ) -> None: ...


class UnitOfWorkPort(Protocol):
    def begin(self) -> AbstractAsyncContextManager[Transaction]: ...

    async def reconcile(
        self,
        match_id: MatchId,
        operation_id: str,
        *,
        expected_last_seq: int,
        events: Sequence[Event],
    ) -> Reconciliation: ...


class MatchRepositoryPort(Protocol):
    async def create(
        self, match_id: MatchId, event: MatchCreated, *, operation_id: str
    ) -> None: ...

    async def read_events(self, match_id: MatchId) -> tuple[Event, ...]: ...

    async def load(self, match_id: MatchId) -> LoadedMatch: ...


class CategoryBank(Protocol):
    """The content library as the runtime needs it. Implemented in plan 6.

    It draws identifiers and nothing else: assembling a `DealPlan` — which
    cells go to whom, which group ids they carry, where each secret lands —
    is the materialiser's job, because those are rules (§3.4), not content.

    Every draw takes the transaction it must happen inside. §5.3 selects
    content under `FOR SHARE`, and those locks are released when the
    transaction ends — which is why §6.3 replays a retried attempt whole
    rather than reusing a pack drawn under locks that are already gone.
    """

    async def draw_categories(
        self, tx: "Transaction", count: int, *, exclude: frozenset[CategoryId]
    ) -> tuple[CategoryId, ...]: ...

    async def draw_images(
        self, tx: "Transaction", category: CategoryId, count: int
    ) -> tuple[ImageId, ...]: ...


class ContentExhausted(Exception):
    """The library cannot supply what was asked for.

    §8 calls image exhaustion a content defect rather than a domain
    transition, and §6.3 makes a content shortfall «обычный отказ, не
    авария» — so this is a rejection the operator sees, never a quarantine.
    """
