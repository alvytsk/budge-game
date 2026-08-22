from podvinsya.domain.board import Cell, groups_are_adjacent
from podvinsya.domain.ids import GroupId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import Group, MatchState


def time_bonus_ms(group: Group, settings: MatchSettings) -> int:
    """+(N-1) seconds for a group of N cells, capped. Spec §2.5."""
    return min(settings.bonus_cap_ms, (len(group.cells) - 1) * 1000)


def starting_budget_ms(group: Group, settings: MatchSettings) -> int:
    return settings.base_ms + time_bonus_ms(group, settings)


def group_containing(state: MatchState, cell: Cell) -> Group:
    for group in state.groups.values():
        if cell in group.cells:
            return group
    raise KeyError(cell)


def legal_targets(state: MatchState, attacking_group: GroupId) -> frozenset[GroupId]:
    attacker = state.groups[attacking_group]
    return frozenset(
        candidate.id
        for candidate in state.groups.values()
        if candidate.owner != attacker.owner
        and groups_are_adjacent(attacker.cells, candidate.cells, state.board)
    )


def next_turn(state: MatchState) -> tuple[int, int]:
    """Return (turn_index, round_no) for the next living player.

    Eliminated players are skipped. The round number increases whenever the
    cursor wraps past the end of the fixed turn order.
    """
    size = len(state.turn_order)
    index = state.turn_index
    round_no = state.round_no
    for _ in range(size):
        index += 1
        if index >= size:
            index = 0
            round_no += 1
        if not state.player(state.turn_order[index]).eliminated:
            return index, round_no
    return state.turn_index, state.round_no
