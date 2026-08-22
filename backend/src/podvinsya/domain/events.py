from dataclasses import dataclass
from datetime import datetime

from podvinsya.domain.board import BoardSize, Cell
from podvinsya.domain.budgets import Budgets
from podvinsya.domain.context import DealtCell
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, PlayerId
from podvinsya.domain.settings import MatchSettings


@dataclass(frozen=True, slots=True)
class MatchCreated:
    board: BoardSize
    settings: MatchSettings
    player_count: int


@dataclass(frozen=True, slots=True)
class PlayerAdded:
    player_id: PlayerId
    name: str
    colour: str


@dataclass(frozen=True, slots=True)
class SecretAssigned:
    player_id: PlayerId
    category: CategoryId


@dataclass(frozen=True, slots=True)
class BoardDealt:
    cells: tuple[DealtCell, ...]


@dataclass(frozen=True, slots=True)
class MatchStarted:
    turn_order: tuple[PlayerId, ...]


@dataclass(frozen=True, slots=True)
class AttackDeclared:
    attacker: PlayerId
    defender: PlayerId
    attacking_group: GroupId
    defending_group: GroupId
    category: CategoryId
    image_order: tuple[ImageId, ...]
    budgets: Budgets


@dataclass(frozen=True, slots=True)
class DuelStarted:
    anchor: datetime


@dataclass(frozen=True, slots=True)
class AnswerAccepted:
    player: PlayerId
    image_index: int
    charged_ms: int
    next_answering: PlayerId
    anchor: datetime


@dataclass(frozen=True, slots=True)
class PassUsed:
    player: PlayerId
    image_index: int
    charged_ms: int
    penalty_ms: int
    anchor: datetime | None


@dataclass(frozen=True, slots=True)
class DuelPaused:
    charged_ms: int


@dataclass(frozen=True, slots=True)
class DuelResumed:
    anchor: datetime


@dataclass(frozen=True, slots=True)
class JudgementUndone:
    undone_seq: int
    budgets: Budgets
    answering: PlayerId
    image_index: int
    anchor: datetime | None


@dataclass(frozen=True, slots=True)
class DuelResolved:
    winner: PlayerId
    loser: PlayerId
    surviving_group: GroupId
    absorbed_group: GroupId
    absorbed_cells: frozenset[Cell]
    burned_category: CategoryId


@dataclass(frozen=True, slots=True)
class PlayerEliminated:
    player_id: PlayerId


@dataclass(frozen=True, slots=True)
class MatchWon:
    player_id: PlayerId


Event = (
    MatchCreated
    | PlayerAdded
    | SecretAssigned
    | BoardDealt
    | MatchStarted
    | AttackDeclared
    | DuelStarted
    | AnswerAccepted
    | PassUsed
    | DuelPaused
    | DuelResumed
    | JudgementUndone
    | DuelResolved
    | PlayerEliminated
    | MatchWon
)
