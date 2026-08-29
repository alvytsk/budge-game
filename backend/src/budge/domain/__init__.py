from budge.domain.board import is_connected
from budge.domain.decide import decide
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.evolve import evolve, fold
from budge.domain.genesis import create_initial_state
from budge.domain.state import MatchState, MatchStatus

__all__ = [
    "Rejected",
    "RejectionReason",
    "check_invariants",
    "create_initial_state",
    "decide",
    "evolve",
    "fold",
]


def check_invariants(state: MatchState) -> None:
    """The three invariants of spec §2.8. Cheap enough to assert after every event."""
    if state.status is MatchStatus.SETUP and not state.groups:
        return

    covered = [cell for group in state.groups.values() for cell in group.cells]
    expected = state.board.cells()
    assert len(covered) == len(expected), (
        f"partition broken: {len(covered)} cells covered, board has {len(expected)}"
    )
    assert set(covered) == set(expected), "partition broken: cells overlap or are missing"

    for group in state.groups.values():
        assert is_connected(group.cells), f"group {group.id} is not orthogonally connected"

    categories = [group.category for group in state.groups.values()]
    assert len(set(categories)) == len(categories), "two groups share a category"
    assert set(categories).isdisjoint(state.played_categories), (
        "a played category is still on the board"
    )
    assert len(state.groups) + len(state.played_categories) == state.board.cell_count, (
        "groups and unplayed categories no longer fall in lockstep"
    )
