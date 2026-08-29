from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from budge.domain.board import BoardSize, Cell
from budge.domain.budgets import Budgets
from budge.domain.ids import CategoryId, GroupId, ImageId, MatchId, PlayerId
from budge.domain.settings import MatchSettings


class MatchStatus(StrEnum):
    SETUP = "setup"
    RUNNING = "running"
    FINISHED = "finished"


class DuelPhase(StrEnum):
    DECLARED = "declared"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class Player:
    id: PlayerId
    name: str
    colour: str
    eliminated: bool = False


@dataclass(frozen=True, slots=True)
class Group:
    id: GroupId
    owner: PlayerId
    category: CategoryId
    cells: frozenset[Cell]
    revealed: bool


@dataclass(frozen=True, slots=True)
class Duel:
    attacker: PlayerId
    defender: PlayerId
    attacking_group: GroupId
    defending_group: GroupId
    category: CategoryId
    image_order: tuple[ImageId, ...]
    index: int
    answering: PlayerId
    budgets: Budgets
    anchor: datetime | None
    phase: DuelPhase

    @property
    def paused(self) -> bool:
        return self.phase is DuelPhase.RUNNING and self.anchor is None

    def opponent_of(self, player: PlayerId) -> PlayerId:
        return self.defender if player == self.attacker else self.attacker


@dataclass(frozen=True, slots=True)
class MatchState:
    id: MatchId
    seq: int
    status: MatchStatus
    board: BoardSize
    settings: MatchSettings
    player_count: int = 0
    players: tuple[Player, ...] = ()
    secrets: Mapping[PlayerId, CategoryId] = field(default_factory=dict)
    turn_order: tuple[PlayerId, ...] = ()
    turn_index: int = 0
    round_no: int = 0
    groups: Mapping[GroupId, Group] = field(default_factory=dict)
    played_categories: frozenset[CategoryId] = frozenset()
    duel: Duel | None = None
    winner: PlayerId | None = None

    def player(self, player_id: PlayerId) -> Player:
        for candidate in self.players:
            if candidate.id == player_id:
                return candidate
        raise KeyError(player_id)

    def current_player(self) -> PlayerId:
        return self.turn_order[self.turn_index]

    def groups_of(self, player: PlayerId) -> tuple[Group, ...]:
        return tuple(g for g in self.groups.values() if g.owner == player)

    def active_players(self) -> tuple[Player, ...]:
        return tuple(p for p in self.players if not p.eliminated)
