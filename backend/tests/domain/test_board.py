import pytest

from podvinsya.domain.board import (
    BoardSize,
    Cell,
    groups_are_adjacent,
    is_connected,
    orthogonal_neighbours,
    validate_board,
)
from podvinsya.domain.errors import Rejected, RejectionReason


def test_board_enumerates_every_cell() -> None:
    board = BoardSize(width=4, height=6)
    cells = board.cells()
    assert len(cells) == 24
    assert len(set(cells)) == 24
    assert Cell(0, 0) in cells
    assert Cell(3, 5) in cells
    assert not board.contains(Cell(4, 0))
    assert not board.contains(Cell(-1, 0))


def test_corner_cell_has_two_neighbours() -> None:
    board = BoardSize(width=4, height=6)
    assert set(orthogonal_neighbours(Cell(0, 0), board)) == {Cell(1, 0), Cell(0, 1)}


def test_interior_cell_has_four_neighbours_and_no_diagonals() -> None:
    board = BoardSize(width=4, height=6)
    neighbours = set(orthogonal_neighbours(Cell(1, 1), board))
    assert neighbours == {Cell(0, 1), Cell(2, 1), Cell(1, 0), Cell(1, 2)}
    assert Cell(0, 0) not in neighbours


def test_groups_touching_orthogonally_are_adjacent() -> None:
    board = BoardSize(width=4, height=6)
    assert groups_are_adjacent(frozenset({Cell(0, 0)}), frozenset({Cell(1, 0)}), board)


def test_groups_touching_only_diagonally_are_not_adjacent() -> None:
    board = BoardSize(width=4, height=6)
    assert not groups_are_adjacent(frozenset({Cell(0, 0)}), frozenset({Cell(1, 1)}), board)


def test_connectivity() -> None:
    assert is_connected(frozenset({Cell(0, 0), Cell(1, 0), Cell(1, 1)}))
    assert not is_connected(frozenset({Cell(0, 0), Cell(2, 0)}))
    assert is_connected(frozenset({Cell(0, 0)}))
    assert is_connected(frozenset())


@pytest.mark.parametrize(
    ("width", "height", "players", "reason"),
    [
        (2, 6, 2, RejectionReason.BOARD_INVALID),
        (6, 2, 2, RejectionReason.BOARD_INVALID),
        (6, 7, 2, RejectionReason.BOARD_INVALID),
        (3, 4, 5, RejectionReason.PLAYER_COUNT_INVALID),
        (3, 4, 0, RejectionReason.PLAYER_COUNT_INVALID),
        (3, 5, 2, RejectionReason.BOARD_NOT_DIVISIBLE),
    ],
)
def test_invalid_boards_are_rejected(
    width: int, height: int, players: int, reason: RejectionReason
) -> None:
    with pytest.raises(Rejected) as excinfo:
        validate_board(BoardSize(width=width, height=height), players)
    assert excinfo.value.reason is reason


@pytest.mark.parametrize(
    ("width", "height", "players"), [(3, 4, 2), (3, 6, 3), (4, 6, 4), (6, 6, 4)]
)
def test_default_boards_are_valid(width: int, height: int, players: int) -> None:
    validate_board(BoardSize(width=width, height=height), players)
