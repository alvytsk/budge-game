from dataclasses import dataclass

from podvinsya.domain.board import BoardSize
from podvinsya.domain.ids import CategoryId, GroupId, PlayerId
from podvinsya.domain.settings import MatchSettings


@dataclass(frozen=True, slots=True)
class CreateMatch:
    board: BoardSize
    settings: MatchSettings
    player_count: int


@dataclass(frozen=True, slots=True)
class AddPlayer:
    player_id: PlayerId
    name: str
    colour: str


@dataclass(frozen=True, slots=True)
class AssignSecret:
    player_id: PlayerId
    category: CategoryId


@dataclass(frozen=True, slots=True)
class DealBoard:
    pass


@dataclass(frozen=True, slots=True)
class StartMatch:
    pass


@dataclass(frozen=True, slots=True)
class DeclareAttack:
    attacking_group: GroupId
    defending_group: GroupId


@dataclass(frozen=True, slots=True)
class StartDuel:
    pass


@dataclass(frozen=True, slots=True)
class JudgeCorrect:
    pass


@dataclass(frozen=True, slots=True)
class JudgePass:
    pass


@dataclass(frozen=True, slots=True)
class PauseDuel:
    pass


@dataclass(frozen=True, slots=True)
class ResumeDuel:
    pass


@dataclass(frozen=True, slots=True)
class UndoLastJudgement:
    pass


@dataclass(frozen=True, slots=True)
class ExpireTimer:
    # The domain ignores this. It cannot check it: Duel does not record which
    # seq set its anchor, so there is nothing here to compare against. Nor does
    # it need to -- spec 4.2 makes the clock authoritative, so a stale timer is
    # already harmless: decide resolves only if now has actually passed the
    # deadline, whatever identifier arrived with the command. Honouring
    # deadline_id (spec 4.3) is the runtime's job, where the scheduler knows
    # which task it cancelled.
    deadline_id: int


Command = (
    CreateMatch
    | AddPlayer
    | AssignSecret
    | DealBoard
    | StartMatch
    | DeclareAttack
    | StartDuel
    | JudgeCorrect
    | JudgePass
    | PauseDuel
    | ResumeDuel
    | UndoLastJudgement
    | ExpireTimer
)
