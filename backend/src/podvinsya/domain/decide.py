from podvinsya.domain.actions import AddPlayer, AssignSecret, Command, CreateMatch
from podvinsya.domain.board import validate_board
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.events import Event, MatchCreated, PlayerAdded, SecretAssigned
from podvinsya.domain.state import MatchState, MatchStatus


def decide(state: MatchState, command: Command, ctx: DecisionContext) -> tuple[Event, ...]:
    match command:
        case CreateMatch():
            return _create_match(state, command)
        case AddPlayer():
            return _add_player(state, command)
        case AssignSecret():
            return _assign_secret(state, command)
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
