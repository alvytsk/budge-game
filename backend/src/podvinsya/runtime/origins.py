"""Who is waiting for a command's outcome, and how they are told.

Two implementations cover everything. `FutureOrigin` serves a caller that
awaits a result — plan 4's REST replies and WebSocket acknowledgements.
`SystemOrigin` serves commands the server issues to itself: deadline
expiries, watchdog re-arms, the recovery pause. Nobody waits on those, but
the loop still resolves them unconditionally: a nullable origin would put a
branch on every resolution path in the loop, and the one that got forgotten
would be a hung request.
"""

import asyncio
import logging
from dataclasses import dataclass
from collections.abc import Sequence
from uuid import uuid4

from podvinsya.domain.actions import Command
from podvinsya.domain.errors import RejectionReason
from podvinsya.domain.events import Event
from podvinsya.services.ports import Origin, RuntimeCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Accepted:
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class NoOp:
    """`decide` produced nothing. Nothing was persisted and nothing is
    broadcast — and this is deliberately not an error: a stale expiry or a
    duplicate is a benign race, not a fault."""


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class Failed:
    code: RuntimeCode
    message: str


CommandOutcome = Accepted | NoOp | Rejected | Failed


class SystemOrigin:
    """A command the server issued to itself. `label` names the issuer, so
    a rejection that should never happen is greppable."""

    def __init__(self, label: str) -> None:
        self._label = label

    def resolve_ok(self, events: Sequence[Event]) -> None:
        return None

    def resolve_noop(self) -> None:
        return None

    def resolve_rejected(self, reason: RejectionReason) -> None:
        # A server-issued command being rejected means the server's own
        # model of the match disagreed with the domain's. That is a bug
        # worth finding, and silence is how it stays unfound.
        self._warn("%s command rejected: %s", self._label, reason.value)

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self._warn("%s command failed: %s — %s", self._label, code.value, message)

    @staticmethod
    def _warn(message: str, *args: object) -> None:
        # `logging.config.fileConfig` — which Alembic's `env.py` calls, in
        # process, whenever a migration runs programmatically — disables by
        # default every logger that already exists and is not named in its
        # config file. This module's logger is created at import time and
        # is not named in `alembic.ini`, so a migration run anywhere in the
        # process (a boot-time upgrade, a test session that exercises the
        # db layer before this one) would otherwise silence it permanently.
        # A warning this module emits is not optional, so undo that here
        # rather than trust that nothing else in the process ever migrates.
        logger.disabled = False
        logger.warning(message, *args)


class FutureOrigin:
    """A caller awaiting an outcome.

    Every resolution path funnels through `_settle`, which is what makes
    the exactly-once and never-raises guarantees a property of one place
    rather than of four.
    """

    def __init__(self) -> None:
        self._future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()

    async def result(self) -> CommandOutcome:
        return await self._future

    def abandon(self) -> None:
        """The caller went away. Used by plan 4 when a request is dropped
        before its command has been consumed."""
        self._future.cancel()

    def _settle(self, outcome: CommandOutcome) -> None:
        if self._future.done():
            # Already resolved, or the caller abandoned it. Either way the
            # command's real outcome is durable and this delivery is not
            # worth an exception on the loop's own thread.
            return
        try:
            self._future.set_result(outcome)
        except asyncio.InvalidStateError:  # pragma: no cover - raced cancellation
            logger.info("origin abandoned before its outcome could be delivered")

    def resolve_ok(self, events: Sequence[Event]) -> None:
        self._settle(Accepted(tuple(events)))

    def resolve_noop(self) -> None:
        self._settle(NoOp())

    def resolve_rejected(self, reason: RejectionReason) -> None:
        self._settle(Rejected(reason))

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self._settle(Failed(code, message))


@dataclass(frozen=True, slots=True)
class QueuedCommand:
    """One command, its server-minted identity, and whoever is waiting."""

    command: Command
    operation_id: str
    origin: Origin

    @classmethod
    def issue(cls, command: Command, origin: Origin) -> "QueuedCommand":
        """Mint the `operation_id` here and nowhere else.

        §5.1 makes this the server's job for every command without
        exception — WebSocket, REST, timer, watchdog. A client-supplied
        value is untrusted input: repeating someone else's would make the
        reconciliation of an ambiguous commit conclude that the batch it
        was looking for had already been written.
        """
        return cls(command=command, operation_id=str(uuid4()), origin=origin)
