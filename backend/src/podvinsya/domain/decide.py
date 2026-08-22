from collections import Counter
from datetime import datetime

from podvinsya.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    CreateMatch,
    DealBoard,
    DeclareAttack,
    JudgeCorrect,
    StartDuel,
    StartMatch,
)
from podvinsya.domain.board import validate_board
from podvinsya.domain.budgets import Budgets
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.events import (
    AnswerAccepted,
    AttackDeclared,
    BoardDealt,
    DuelStarted,
    Event,
    MatchCreated,
    MatchStarted,
    PlayerAdded,
    SecretAssigned,
)
from podvinsya.domain.ids import PlayerId
from podvinsya.domain.rules import legal_targets, starting_budget_ms
from podvinsya.domain.state import Duel, DuelPhase, MatchState, MatchStatus
from podvinsya.domain.timing import elapsed_ms


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
        case DeclareAttack():
            return _declare_attack(state, command, ctx)
        case StartDuel():
            return _start_duel(state, ctx)
        case JudgeCorrect():
            return _judge_correct(state, ctx)
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
        raise Rejected(RejectionReason.DEAL_INVALID)

    categories = [dealt.category for dealt in plan.cells]
    if len(set(categories)) != len(categories):
        raise Rejected(RejectionReason.DEAL_INVALID)

    group_ids = [dealt.group_id for dealt in plan.cells]
    if len(set(group_ids)) != len(group_ids):
        raise Rejected(RejectionReason.DEAL_INVALID)

    per_player = state.board.cell_count // state.player_count
    owners = Counter(dealt.owner for dealt in plan.cells)
    if set(owners) != {p.id for p in state.players} or set(owners.values()) != {per_player}:
        raise Rejected(RejectionReason.DEAL_INVALID)

    secret_owner = {category: owner for owner, category in state.secrets.items()}
    for dealt in plan.cells:
        expected_owner = secret_owner.get(dealt.category)
        if expected_owner is not None:
            if dealt.owner != expected_owner or dealt.revealed:
                raise Rejected(RejectionReason.DEAL_INVALID)
        elif not dealt.revealed:
            raise Rejected(RejectionReason.DEAL_INVALID)
    dealt_categories = {d.category for d in plan.cells}
    if len(dealt_categories & set(state.secrets.values())) != len(state.secrets):
        raise Rejected(RejectionReason.DEAL_INVALID)

    return (BoardDealt(cells=plan.cells),)


def _start_match(state: MatchState) -> tuple[Event, ...]:
    _require_setup(state)
    if len(state.groups) != state.board.cell_count:
        raise Rejected(RejectionReason.DEAL_INVALID)
    return (MatchStarted(turn_order=tuple(p.id for p in state.players)),)


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
    raise NotImplementedError("duel resolution lands in task 11")


def _judge_correct(state: MatchState, ctx: DecisionContext) -> tuple[Event, ...]:
    duel = _require_live_duel(state)
    budgets, charged = _charge(duel, ctx.now)
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
