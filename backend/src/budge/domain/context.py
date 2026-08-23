from dataclasses import dataclass
from datetime import datetime

from budge.domain.board import Cell
from budge.domain.budgets import Budgets
from budge.domain.ids import CategoryId, GroupId, ImageId, PlayerId


@dataclass(frozen=True, slots=True)
class DealtCell:
    cell: Cell
    owner: PlayerId
    category: CategoryId
    group_id: GroupId
    revealed: bool


@dataclass(frozen=True, slots=True)
class DealPlan:
    cells: tuple[DealtCell, ...]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """State of the duel immediately before one judging event.

    The runtime derives these from the event log and hands them in; the domain
    never reads the log itself.
    """

    seq: int
    budgets: Budgets
    answering: PlayerId
    image_index: int


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Every non-deterministic input the domain needs, supplied as a value.

    The domain never reads a clock and never draws a random number: the caller
    resolves both and hands the results in, which is what makes decide() pure
    and every test deterministic.
    """

    now: datetime
    deal: DealPlan | None = None
    image_order: tuple[ImageId, ...] | None = None
    duel_journal: tuple[JournalEntry, ...] = ()
