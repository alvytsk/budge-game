from dataclasses import dataclass
from datetime import datetime

from podvinsya.domain.board import Cell
from podvinsya.domain.ids import CategoryId, GroupId, ImageId, PlayerId


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
class DecisionContext:
    """Every non-deterministic input the domain needs, supplied as a value.

    The domain never reads a clock and never draws a random number: the caller
    resolves both and hands the results in, which is what makes decide() pure
    and every test deterministic.
    """

    now: datetime
    deal: DealPlan | None = None
    image_order: tuple[ImageId, ...] | None = None
