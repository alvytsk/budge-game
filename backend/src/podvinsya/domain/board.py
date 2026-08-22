from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

from podvinsya.domain.errors import Rejected, RejectionReason

MAX_CELLS = 36
MIN_SIDE = 3


class Cell(NamedTuple):
    col: int
    row: int


@dataclass(frozen=True, slots=True)
class BoardSize:
    width: int
    height: int

    @property
    def cell_count(self) -> int:
        return self.width * self.height

    def cells(self) -> tuple[Cell, ...]:
        return tuple(
            Cell(col, row) for row in range(self.height) for col in range(self.width)
        )

    def contains(self, cell: Cell) -> bool:
        return 0 <= cell.col < self.width and 0 <= cell.row < self.height


_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def orthogonal_neighbours(cell: Cell, board: BoardSize) -> tuple[Cell, ...]:
    candidates = (Cell(cell.col + dc, cell.row + dr) for dc, dr in _OFFSETS)
    return tuple(c for c in candidates if board.contains(c))


def groups_are_adjacent(
    left: frozenset[Cell], right: frozenset[Cell], board: BoardSize
) -> bool:
    return any(
        neighbour in right for cell in left for neighbour in orthogonal_neighbours(cell, board)
    )


def is_connected(cells: frozenset[Cell]) -> bool:
    if len(cells) <= 1:
        return True
    start = next(iter(cells))
    seen = {start}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for dc, dr in _OFFSETS:
            neighbour = Cell(cell.col + dc, cell.row + dr)
            if neighbour in cells and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return len(seen) == len(cells)


def validate_board(board: BoardSize, player_count: int) -> None:
    if board.width < MIN_SIDE or board.height < MIN_SIDE:
        raise Rejected(RejectionReason.BOARD_INVALID)
    if board.cell_count > MAX_CELLS:
        raise Rejected(RejectionReason.BOARD_INVALID)
    if player_count > 0 and board.cell_count % player_count != 0:
        raise Rejected(RejectionReason.BOARD_NOT_DIVISIBLE)
    if player_count < 2 or player_count > 4:
        raise Rejected(RejectionReason.PLAYER_COUNT_INVALID)
