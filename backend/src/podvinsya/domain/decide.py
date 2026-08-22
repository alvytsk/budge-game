from collections import Counter

from podvinsya.domain.actions import (
    AddPlayer,
    AssignSecret,
    Command,
    CreateMatch,
    DealBoard,
    StartMatch,
)
from podvinsya.domain.board import validate_board
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.events import (
    BoardDealt,
    Event,
    MatchCreated,
    MatchStarted,
    PlayerAdded,
    SecretAssigned,
)
from podvinsya.domain.state import MatchState, MatchStatus


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
