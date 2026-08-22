from podvinsya.domain.actions import Command, CreateMatch
from podvinsya.domain.board import validate_board
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.state import MatchState


def decide(state: MatchState, command: Command, ctx: DecisionContext) -> tuple[Event, ...]:
    match command:
        case CreateMatch():
            return _create_match(state, command)
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
