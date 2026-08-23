"""§4.4: a match recovered mid-duel comes back paused, and the outage is
charged to nobody.

`MatchRepository.load` folds the log and stops there -- its own docstring
says so plainly: «§4.4's pause-on-recovery is deliberately absent. It emits
an event, and this layer emits nothing.» This module is that layer. If the
folded state shows a duel genuinely running -- phase RUNNING, anchor not
empty -- it issues one ordinary `PauseDuel`, through the ordinary
commit path, before handing the caller a runtime to run.

The one twist is *when* that `PauseDuel` is decided. Decided at the moment
of recovery, `elapsed_ms(anchor, now, remaining)` would charge the entire
outage -- however long the process was down -- to whoever was answering.
Decided at the duel's own anchor, `elapsed_ms` returns zero and both
remainders come back exactly as the log left them: the outage becomes a
real `DuelPaused` in the log, not stolen seconds. `Materialiser.build`'s
`at` parameter exists for exactly this call.

The ordering knot this module has to untie: `MatchRuntime` needs a state to
hold, but the state it should hold -- paused, if a duel was running -- can
only be produced by running a command *through* the commit machinery a
runtime wraps. Building the eventual runtime first and then reaching into
its internals to drive one command through it would need either a new
public method on `MatchRuntime` (out of scope here -- this task creates
exactly one file) or calling its private `_consume`. Both spend a knot on
production code that a single throwaway `CommitPath` sidesteps entirely:
`_pause_at_anchor` runs the one `PauseDuel` commit standalone, using the
same `decide`/append/retry path `CommitPath.run` always uses, with an
anchored materialiser plugged in in place of the ordinary one. Only once
that has produced the paused state -- or been skipped, because there was
nothing to pause -- does this module build the `MatchRuntime` the caller
actually gets, wired with the ordinary, wall-clock-reading materialiser for
every command it processes from here on.
"""

from collections.abc import Sequence
from datetime import datetime
from random import Random

from budge.domain.actions import Command, PauseDuel
from budge.domain.context import DecisionContext
from budge.domain.errors import RejectionReason
from budge.domain.events import Event
from budge.domain.evolve import fold
from budge.domain.ids import MatchId
from budge.domain.state import DuelPhase, MatchState
from budge.runtime.commit import CommitPath
from budge.runtime.materialiser import Materialiser
from budge.runtime.match import MatchRuntime, wire_deadline_fire
from budge.runtime.origins import Accepted, QueuedCommand
from budge.runtime.scheduler import DeadlineScheduler
from budge.services.ports import (
    Broadcaster,
    Clock,
    MatchRepositoryPort,
    RuntimeCode,
    Transaction,
    UnitOfWorkPort,
)


class _UnresolvedOrigin:
    """Satisfies `QueuedCommand.issue`'s `Origin` parameter without being
    one in any real sense.

    `CommitPath.run` -- unlike `MatchRuntime._consume` -- never calls any
    method on the origin it is handed; only `_consume` resolves one, and
    this path never reaches `_consume`. So nothing constructed here is
    ever resolved, and nothing here ever logs: the `RuntimeError` raised
    by `_pause_at_anchor` below is the only signal a caller gets if the
    pause is not accepted. Every method is a no-op for that reason, not as
    a shortcut.
    """

    def resolve_ok(self, events: Sequence[Event]) -> None:
        return None

    def resolve_noop(self) -> None:
        return None

    def resolve_rejected(self, reason: RejectionReason) -> None:
        return None

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        return None


class _AnchoredMaterialiser(Materialiser):
    """Pins every `build` call to one fixed instant.

    Private to this module and built fresh for the single recovery commit:
    `CommitPath.run` never passes `at` itself, so overriding `build` to
    ignore whatever it is given and always answer with `self._at` is enough
    -- there is no other caller to confuse. Subclassing `Materialiser`
    rather than composing behind a protocol is what lets this stand in
    wherever `CommitPath` expects one: `CommitPath.__init__` types that
    parameter as the concrete class, not a structural protocol.
    """

    def __init__(self, delegate: Materialiser, at: datetime) -> None:
        # Deliberately not calling Materialiser.__init__: every method this
        # class actually uses is overridden below, and the delegate already
        # holds the real clock, repository, bank and random this recovery
        # run needs.
        self._delegate = delegate
        self._at = at

    async def build(
        self,
        state: MatchState,
        command: Command,
        tx: Transaction,
        *,
        at: datetime | None = None,
    ) -> DecisionContext:
        return await self._delegate.build(state, command, tx, at=self._at)


def _needs_pausing(state: MatchState) -> bool:
    """§4.4's own condition, read directly off the folded state: phase
    RUNNING and an anchor that is not empty. `Duel.paused` names the same
    thing the other way around (phase RUNNING, anchor None) -- checking it
    here rather than re-deriving it is what keeps recovery from ever
    issuing `PauseDuel` against a duel the domain would refuse it on."""
    duel = state.duel
    return duel is not None and duel.phase is DuelPhase.RUNNING and not duel.paused


async def _pause_at_anchor(
    state: MatchState,
    uow: UnitOfWorkPort,
    materialiser: Materialiser,
    clock: Clock,
    random: Random,
) -> MatchState:
    """Run one `PauseDuel` through a throwaway commit path, decided as of
    the duel's own anchor, and return the state it folds to.

    This calls `CommitPath.run` directly rather than going through
    `MatchRuntime._consume`, so no origin is ever resolved on this path --
    `QueuedCommand.issue` still needs one, and `_UnresolvedOrigin` is
    exactly that: an inert placeholder, not a real destination for the
    outcome. The raise below is the only signal.
    """
    duel = state.duel
    assert duel is not None and duel.anchor is not None, "_needs_pausing already checked this"
    anchored = _AnchoredMaterialiser(materialiser, duel.anchor)
    commit_path = CommitPath(uow, anchored, clock, random)
    queued = QueuedCommand.issue(PauseDuel(), _UnresolvedOrigin())
    outcome = await commit_path.run(state, queued)
    if not isinstance(outcome, Accepted):
        # Nothing upstream of this raise has recorded or logged the
        # outcome -- see _UnresolvedOrigin -- so this message, carrying
        # the outcome itself, is the only record a caller ever gets that
        # a duel this recovery just loaded could not be paused.
        raise RuntimeError(f"recovery could not pause {state.id}: {outcome!r}")
    return fold(state, outcome.events)


async def recover(
    match_id: MatchId,
    repository: MatchRepositoryPort,
    uow: UnitOfWorkPort,
    materialiser: Materialiser,
    clock: Clock,
    random: Random,
    broadcaster: Broadcaster,
) -> MatchRuntime:
    """Fold `match_id`'s log and hand back a runtime holding the result.

    If the log left a duel genuinely running, the runtime this returns
    holds the *paused* state, and the log already carries the `DuelPaused`
    that says so -- both happen before this function returns, never after.
    The caller is what starts `.run()` on the result; this function never
    does, so nothing can race the recovery pause.
    """
    loaded = await repository.load(match_id)
    state = loaded.state

    if _needs_pausing(state):
        state = await _pause_at_anchor(state, uow, materialiser, clock, random)

    commit_path = CommitPath(uow, materialiser, clock, random)
    runtime_box: list[MatchRuntime] = []
    fire = wire_deadline_fire(lambda command, origin: runtime_box[0].submit(command, origin))
    scheduler = DeadlineScheduler(clock, fire)
    runtime = MatchRuntime(match_id, state, commit_path, scheduler, broadcaster)
    runtime_box.append(runtime)
    return runtime
