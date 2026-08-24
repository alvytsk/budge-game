from collections import Counter
from datetime import datetime

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
    ResetMatch,
    ResumeDuel,
    StartDuel,
    StartMatch,
    UndoLastJudgement,
)
from budge.domain.board import validate_board
from budge.domain.budgets import Budgets
from budge.domain.context import DecisionContext
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.events import (
    AnswerAccepted,
    AttackDeclared,
    BoardDealt,
    DuelPaused,
    DuelResolved,
    DuelResumed,
    DuelStarted,
    Event,
    JudgementUndone,
    MatchCreated,
    MatchReset,
    MatchStarted,
    MatchWon,
    PassUsed,
    PlayerAdded,
    PlayerEliminated,
    SecretAssigned,
)
from budge.domain.ids import PlayerId
from budge.domain.rules import legal_targets, starting_budget_ms
from budge.domain.state import Duel, DuelPhase, MatchState, MatchStatus
from budge.domain.timing import elapsed_ms, is_expired


def decide(state: MatchState, command: Command, ctx: DecisionContext) -> tuple[Event, ...]:
    match command:
        case CreateMatch():
            return _create_match(state, command)
        case AddPlayer():
            return _add_player(state, command)
        case AssignSecret():
            return _assign_secret(state, command)
        case DealBoard():
            return _deal_board(state, ctx)
        case StartMatch():
            return _start_match(state)
        case ResetMatch():
            return _reset_match(state, command)
        case DeclareAttack():
            return _declare_attack(state, command, ctx)
        case StartDuel():
            return _start_duel(state, ctx)
        case JudgeCorrect():
            return _judge_correct(state, ctx)
        case JudgePass():
            return _judge_pass(state, ctx)
        case PauseDuel():
            return _pause_duel(state, ctx)
        case ResumeDuel():
            return _resume_duel(state, ctx)
        case ExpireTimer():
            return _expire_timer(state, ctx)
        case UndoLastJudgement():
            return _undo(state, ctx)
        case _:
            raise NotImplementedError(type(command).__name__)


def _create_match(state: MatchState, command: CreateMatch) -> tuple[Event, ...]:
    if state.seq != 0:
        raise Rejected(RejectionReason.WRONG_STATUS)
    validate_board(command.board, command.player_count)
    return (
        MatchCreated(
            board=command.board,
            settings=command.settings,
            player_count=command.player_count,
        ),
    )


def _require_setup(state: MatchState) -> None:
    if state.status is not MatchStatus.SETUP:
        raise Rejected(RejectionReason.WRONG_STATUS)


def _add_player(state: MatchState, command: AddPlayer) -> tuple[Event, ...]:
    _require_setup(state)
    if any(p.id == command.player_id for p in state.players):
        raise Rejected(RejectionReason.DUPLICATE_PLAYER)
    if len(state.players) >= state.player_count:
        raise Rejected(RejectionReason.PLAYER_COUNT_INVALID)
    return (PlayerAdded(command.player_id, command.name, command.colour),)


def _assign_secret(state: MatchState, command: AssignSecret) -> tuple[Event, ...]:
    _require_setup(state)
    if not any(p.id == command.player_id for p in state.players):
        raise Rejected(RejectionReason.UNKNOWN_PLAYER)
    for owner, category in state.secrets.items():
        if category == command.category and owner != command.player_id:
            raise Rejected(RejectionReason.DUPLICATE_CATEGORY)
    if state.secrets.get(command.player_id) == command.category:
        return ()
    return (SecretAssigned(command.player_id, command.category),)


def _deal_board(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    _require_setup(state)
    if len(state.players) != state.player_count:
        raise Rejected(RejectionReason.PLAYER_COUNT_INVALID)
    if any(p.id not in state.secrets for p in state.players):
        raise Rejected(RejectionReason.SECRET_MISSING)
    if ctx.deal is None:
        raise Rejected(RejectionReason.DEAL_INVALID)

    plan = ctx.deal
    cells = [dealt.cell for dealt in plan.cells]
    if sorted(cells) != sorted(state.board.cells()):
        raise Rejected(RejectionReason.DEAL_INCOMPLETE)

    categories = [dealt.category for dealt in plan.cells]
    if len(set(categories)) != len(categories):
        raise Rejected(RejectionReason.DEAL_DUPLICATE_CATEGORY)

    group_ids = [dealt.group_id for dealt in plan.cells]
    if len(set(group_ids)) != len(group_ids):
        raise Rejected(RejectionReason.DEAL_DUPLICATE_GROUP_ID)

    owners = Counter(dealt.owner for dealt in plan.cells)
    if set(owners) != {p.id for p in state.players}:
        raise Rejected(RejectionReason.DEAL_UNKNOWN_OWNER)

    per_player = state.board.cell_count // state.player_count
    if set(owners.values()) != {per_player}:
        raise Rejected(RejectionReason.DEAL_UNBALANCED)

    secret_owner = {category: owner for owner, category in state.secrets.items()}
    for dealt in plan.cells:
        expected_owner = secret_owner.get(dealt.category)
        if expected_owner is not None:
            if dealt.owner != expected_owner:
                raise Rejected(RejectionReason.DEAL_SECRET_MISPLACED)
            if dealt.revealed:
                raise Rejected(RejectionReason.DEAL_SECRET_REVEALED)
        elif not dealt.revealed:
            raise Rejected(RejectionReason.DEAL_CATEGORY_NOT_REVEALED)
    dealt_categories = {d.category for d in plan.cells}
    if len(dealt_categories & set(state.secrets.values())) != len(state.secrets):
        raise Rejected(RejectionReason.DEAL_SECRET_ABSENT)

    return (BoardDealt(cells=plan.cells),)


def _start_match(state: MatchState) -> tuple[Event, ...]:
    _require_setup(state)
    if len(state.groups) != state.board.cell_count:
        raise Rejected(RejectionReason.DEAL_INVALID)
    return (MatchStarted(turn_order=tuple(p.id for p in state.players)),)


def _reset_match(state: MatchState, command: ResetMatch) -> tuple[Event, ...]:
    """§A.4: legal in every phase, and an empty transition when there is
    nothing to reset.

    A rejection here would be worse than an empty event: an operator who
    pressed "Reset" twice would get an error for having gotten what they
    wanted. Precedent: `_assign_secret`, which returns `()` on a repeated
    assignment.
    """
    already_at_the_beginning = (
        state.status is MatchStatus.SETUP
        and not state.groups
        and state.duel is None
        and state.winner is None
        and (command.keep_roster or (not state.players and not state.secrets))
    )
    if already_at_the_beginning:
        return ()
    return (MatchReset(keep_roster=command.keep_roster),)


def _require_running(state: MatchState) -> None:
    if state.status is not MatchStatus.RUNNING:
        raise Rejected(RejectionReason.WRONG_STATUS)


def _declare_attack(
    state: MatchState, command: DeclareAttack, ctx: DecisionContext
) -> tuple[Event, ...]:
    _require_running(state)
    if state.duel is not None:
        raise Rejected(RejectionReason.DUEL_IN_PROGRESS)
    if (
        command.attacking_group not in state.groups
        or command.defending_group not in state.groups
    ):
        raise Rejected(RejectionReason.UNKNOWN_GROUP)

    attacking = state.groups[command.attacking_group]
    defending = state.groups[command.defending_group]
    if attacking.owner != state.current_player():
        raise Rejected(RejectionReason.NOT_YOUR_TURN)
    if defending.owner == attacking.owner:
        raise Rejected(RejectionReason.TARGET_IS_YOURS)
    if command.defending_group not in legal_targets(state, command.attacking_group):
        raise Rejected(RejectionReason.NOT_ADJACENT)
    if not ctx.image_order:
        raise Rejected(RejectionReason.IMAGES_EXHAUSTED)

    budgets = Budgets.of(
        {
            attacking.owner: starting_budget_ms(attacking, state.settings),
            defending.owner: starting_budget_ms(defending, state.settings),
        }
    )
    return (
        AttackDeclared(
            attacker=attacking.owner,
            defender=defending.owner,
            attacking_group=attacking.id,
            defending_group=defending.id,
            category=defending.category,
            image_order=ctx.image_order,
            budgets=budgets,
        ),
    )


def _require_duel(state: MatchState) -> Duel:
    _require_running(state)
    if state.duel is None:
        raise Rejected(RejectionReason.NO_DUEL)
    return state.duel


def _start_duel(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.DECLARED:
        raise Rejected(RejectionReason.DUEL_NOT_DECLARED)
    return (DuelStarted(anchor=ctx.now),)


def _require_live_duel(state: MatchState) -> Duel:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.RUNNING:
        raise Rejected(RejectionReason.DUEL_NOT_RUNNING)
    if duel.paused:
        raise Rejected(RejectionReason.DUEL_PAUSED)
    return duel


def _charge(duel: Duel, now: datetime) -> tuple[Budgets, int]:
    remaining = duel.budgets.get(duel.answering)
    charged = elapsed_ms(duel.anchor, now, remaining)
    return duel.budgets.charge(duel.answering, charged), charged


def _resolve(state: MatchState, duel: Duel, loser: PlayerId) -> tuple[Event, ...]:
    winner = duel.opponent_of(loser)
    attacking = state.groups[duel.attacking_group]
    defending = state.groups[duel.defending_group]
    resolved = DuelResolved(
        winner=winner,
        loser=loser,
        surviving_group=attacking.id,
        absorbed_group=defending.id,
        absorbed_cells=defending.cells,
        burned_category=defending.category,
    )

    loser_groups_left = sum(
        1
        for gid, group in state.groups.items()
        if group.owner == loser and gid not in (attacking.id, defending.id)
    )
    if loser_groups_left > 0:
        return (resolved,)

    eliminated = PlayerEliminated(player_id=loser)
    survivors = [p.id for p in state.players if not p.eliminated and p.id != loser]
    if len(survivors) == 1:
        return (resolved, eliminated, MatchWon(player_id=survivors[0]))
    return (resolved, eliminated)


def _expire_timer(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    if state.duel is None:
        return ()
    duel = state.duel
    if duel.phase is not DuelPhase.RUNNING or duel.paused:
        return ()
    if not is_expired(duel, ctx.now):
        return ()
    return _resolve(state, duel, loser=duel.answering)


def _require_next_image(duel: Duel) -> None:
    """Refuse to advance past the last image drawn at declaration time.

    A duel is not bounded by the image count -- a correct answer costs no
    budget, so the pack can genuinely run dry. Spec 8 calls that a content
    defect and says plainly there is no domain transition for it, so this is a
    refusal handed back to the host, never a game outcome.
    """
    if duel.index + 1 >= len(duel.image_order):
        raise Rejected(RejectionReason.IMAGES_EXHAUSTED)


def _judge_correct(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    if is_expired(duel, ctx.now):
        return _resolve(state, duel, loser=duel.answering)
    _require_next_image(duel)
    budgets, charged = _charge(duel, ctx.now)
    # Not a second clock-authority check -- is_expired above is that one, and
    # while the clock runs forward this branch is unreachable. It defends the
    # backwards clock: with the budget already at zero, now < anchor makes
    # is_expired false and elapsed_ms clamps the negative difference to zero,
    # so the duel would otherwise run on with a player at zero.
    if budgets.get(duel.answering) == 0:
        return _resolve(state, duel, loser=duel.answering)
    return (
        AnswerAccepted(
            player=duel.answering,
            image_index=duel.index,
            charged_ms=charged,
            next_answering=duel.opponent_of(duel.answering),
            anchor=ctx.now,
        ),
    )


def _pause_duel(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    # Spec 4.2 is universal: past the deadline the clock wins whatever command
    # arrived. Without this a late pause would park a duel the answerer had
    # already lost, at zero, with no DuelResolved anywhere in the log.
    if is_expired(duel, ctx.now):
        return _resolve(state, duel, loser=duel.answering)
    _, charged = _charge(duel, ctx.now)
    return (DuelPaused(charged_ms=charged),)


def _resume_duel(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.RUNNING:
        raise Rejected(RejectionReason.DUEL_NOT_RUNNING)
    if not duel.paused:
        raise Rejected(RejectionReason.DUEL_NOT_PAUSED)
    return (DuelResumed(anchor=ctx.now),)


def _undo(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_duel(state)
    if duel.phase is not DuelPhase.RUNNING:
        raise Rejected(RejectionReason.DUEL_NOT_RUNNING)
    # Spec 4.2 again, and 3.7 already concedes that undo cannot recover a duel
    # that has ended. A pause sets the anchor to None and so makes is_expired
    # false, which keeps the host's pause-then-undo route open -- that is what
    # 3.7 means by naming pause the tool for edge moments.
    if is_expired(duel, ctx.now):
        return _resolve(state, duel, loser=duel.answering)
    if not ctx.duel_journal:
        raise Rejected(RejectionReason.NOTHING_TO_UNDO)
    entry = ctx.duel_journal[-1]
    return (
        JudgementUndone(
            undone_seq=entry.seq,
            budgets=entry.budgets,
            answering=entry.answering,
            image_index=entry.image_index,
            # A paused duel stays paused: spec 4.1 defines anchor is None as
            # the pause, so re-anchoring here would restart the clock behind
            # the room's back. PassUsed already models the same thing.
            anchor=None if duel.paused else ctx.now,
        ),
    )


def _judge_pass(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    if is_expired(duel, ctx.now):
        return _resolve(state, duel, loser=duel.answering)
    _require_next_image(duel)
    budgets, charged = _charge(duel, ctx.now)
    penalty = state.settings.pass_penalty_ms
    after_penalty = budgets.charge(duel.answering, penalty)

    if after_penalty.get(duel.answering) == 0:
        pass_event = PassUsed(
            player=duel.answering,
            image_index=duel.index,
            charged_ms=charged,
            penalty_ms=penalty,
            anchor=None,
        )
        return (pass_event, *_resolve(state, duel, loser=duel.answering))

    return (
        PassUsed(
            player=duel.answering,
            image_index=duel.index,
            charged_ms=charged,
            penalty_ms=penalty,
            anchor=ctx.now,
        ),
    )
