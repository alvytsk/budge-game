from dataclasses import replace
from uuid import uuid4

from podvinsya.domain.board import Cell
from podvinsya.domain.ids import CategoryId, GroupId, PlayerId
from podvinsya.domain.rules import (
    group_containing,
    legal_targets,
    starting_budget_ms,
    time_bonus_ms,
)
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import Group

from .conftest import build_running_state


def _group(size: int) -> Group:
    return Group(
        id=GroupId(uuid4()),
        owner=PlayerId(uuid4()),
        category=CategoryId(uuid4()),
        cells=frozenset(Cell(i, 0) for i in range(size)),
        revealed=True,
    )


def test_single_cell_group_gives_no_bonus() -> None:
    assert time_bonus_ms(_group(1), MatchSettings()) == 0


def test_bonus_is_one_second_per_cell_beyond_the_first() -> None:
    assert time_bonus_ms(_group(6), MatchSettings()) == 5_000


def test_bonus_is_capped() -> None:
    settings = MatchSettings()
    assert time_bonus_ms(_group(16), settings) == 15_000
    assert time_bonus_ms(_group(30), settings) == 15_000


def test_starting_budget_is_base_plus_bonus() -> None:
    assert starting_budget_ms(_group(6), MatchSettings()) == 65_000


def test_legal_targets_are_orthogonally_adjacent_enemy_groups() -> None:
    state, players = build_running_state(4)
    corner = group_containing(state, Cell(0, 0))
    targets = legal_targets(state, corner.id)

    right = group_containing(state, Cell(1, 0))
    below = group_containing(state, Cell(0, 1))
    diagonal = group_containing(state, Cell(1, 1))

    assert right.id in targets
    assert below.id in targets
    assert diagonal.id not in targets
    assert all(state.groups[gid].owner != corner.owner for gid in targets)


def test_own_groups_are_never_targets() -> None:
    state, _ = build_running_state(4)
    corner = group_containing(state, Cell(0, 0))
    own = {g.id for g in state.groups.values() if g.owner == corner.owner}
    assert legal_targets(state, corner.id).isdisjoint(own)


def test_merged_group_reaches_further() -> None:
    state, _ = build_running_state(4)
    left = group_containing(state, Cell(0, 0))
    right = group_containing(state, Cell(1, 0))
    merged = replace(left, cells=left.cells | right.cells)
    groups = {gid: g for gid, g in state.groups.items() if gid != right.id}
    groups[merged.id] = merged
    widened = replace(state, groups=groups)

    targets = legal_targets(widened, merged.id)
    assert group_containing(widened, Cell(2, 0)).id in targets
