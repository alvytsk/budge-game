from collections import Counter
from dataclasses import replace
from uuid import uuid4

import pytest

from budge.domain.actions import (
    AddPlayer,
    AssignSecret,
    CreateMatch,
    DealBoard,
    StartMatch,
)
from budge.domain.board import BoardSize
from budge.domain.context import DealPlan
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.genesis import create_initial_state
from budge.domain.ids import CategoryId, MatchId, PlayerId
from budge.domain.settings import MatchSettings
from budge.domain.state import MatchState

from .conftest import apply, build_dealt_state, build_setup_state, make_deal


def test_deal_covers_every_cell_exactly_once() -> None:
    state, _ = build_dealt_state(4)
    covered = [cell for group in state.groups.values() for cell in group.cells]
    assert sorted(covered) == sorted(state.board.cells())


def test_every_player_gets_the_same_number_of_cells() -> None:
    state, players = build_dealt_state(4)
    counts = Counter(
        group.owner for group in state.groups.values() for _ in group.cells
    )
    assert set(counts.values()) == {state.board.cell_count // len(players)}


def test_each_cell_starts_as_its_own_group_with_a_distinct_category() -> None:
    state, _ = build_dealt_state(4)
    assert len(state.groups) == state.board.cell_count
    assert all(len(group.cells) == 1 for group in state.groups.values())
    categories = [group.category for group in state.groups.values()]
    assert len(set(categories)) == len(categories)


def test_each_secret_lands_on_its_owner_and_starts_unrevealed() -> None:
    state, players = build_dealt_state(4)
    for player_id in players:
        secret = state.secrets[player_id]
        holder = next(g for g in state.groups.values() if g.category == secret)
        assert holder.owner == player_id
        assert holder.revealed is False


def test_non_secret_groups_start_revealed() -> None:
    state, _ = build_dealt_state(4)
    secrets = set(state.secrets.values())
    for group in state.groups.values():
        if group.category not in secrets:
            assert group.revealed is True


def test_redeal_replaces_the_previous_deal_entirely() -> None:
    state, players = build_dealt_state(4)
    first_group_ids = set(state.groups)
    second = make_deal(state.board, players, dict(state.secrets))
    state = apply(state, DealBoard(), deal=second)
    assert set(state.groups).isdisjoint(first_group_ids)
    assert len(state.groups) == state.board.cell_count


def test_deal_without_a_plan_is_rejected() -> None:
    state, _ = build_setup_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard())
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def _valid_plan(state: MatchState, players: tuple[PlayerId, ...]) -> DealPlan:
    return make_deal(state.board, players, dict(state.secrets))


def _index_of_secret(plan: DealPlan, secret: CategoryId) -> int:
    return next(i for i, c in enumerate(plan.cells) if c.category == secret)


def _plain_indices(plan: DealPlan, secrets: set[CategoryId]) -> list[int]:
    return [i for i, c in enumerate(plan.cells) if c.category not in secrets]


def test_deal_outside_setup_is_rejected() -> None:
    """Trips only _require_setup: the plan handed in is a valid one."""
    state, players = build_dealt_state(4)
    state = apply(state, StartMatch())
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=_valid_plan(state, players))
    assert excinfo.value.reason is RejectionReason.WRONG_STATUS


def test_deal_before_every_player_has_joined_is_rejected() -> None:
    board = BoardSize(4, 6)
    state = create_initial_state(MatchId(uuid4()), board, MatchSettings())
    state = apply(state, CreateMatch(board=board, settings=MatchSettings(), player_count=4))
    for index in range(3):
        player_id = PlayerId(uuid4())
        state = apply(state, AddPlayer(player_id=player_id, name=f"P{index}", colour="#fff"))
        state = apply(state, AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=()))
    assert excinfo.value.reason is RejectionReason.PLAYER_COUNT_INVALID


def test_deal_missing_a_cell_is_rejected() -> None:
    """One board cell left uncovered, with everything else about the plan intact.

    The last cell is pointed at the first cell's coordinates rather than dropped:
    truncating the plan would also unbalance the per-player counts, so the balance
    guard would catch it and the cells-cover guard could be deleted unnoticed.
    """
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    cells = list(plan.cells)
    cells[-1] = replace(cells[-1], cell=cells[0].cell)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_INCOMPLETE


def test_deal_with_a_duplicate_category_is_rejected() -> None:
    """Two plain cells share a category.

    The donor category is deliberately *not* a secret: copying a secret across
    would land it on the wrong owner too, so the secret-ownership guard would
    catch the plan and this test would say nothing about duplicates.
    """
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    plain = _plain_indices(plan, set(state.secrets.values()))
    cells = list(plan.cells)
    cells[plain[-1]] = replace(cells[plain[-1]], category=cells[plain[0]].category)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_DUPLICATE_CATEGORY


def test_deal_with_a_duplicate_group_id_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    cells = list(plan.cells)
    cells[-1] = replace(cells[-1], group_id=cells[0].group_id)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_DUPLICATE_GROUP_ID


def test_deal_with_an_unknown_owner_is_rejected() -> None:
    """Every cell of one player reassigned to a stranger: the counts stay balanced."""
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    stranger = PlayerId(uuid4())
    cells = [
        replace(c, owner=stranger) if c.owner == players[3] else c for c in plan.cells
    ]
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_UNKNOWN_OWNER


def test_deal_with_unequal_per_player_counts_is_rejected() -> None:
    """One plain cell moved without a swap back: the owner set is still complete."""
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    cells = list(plan.cells)
    moved = next(
        i
        for i, c in enumerate(plan.cells)
        if c.owner == players[1] and c.category not in state.secrets.values()
    )
    cells[moved] = replace(cells[moved], owner=players[0])
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_UNBALANCED


def test_deal_with_a_secret_on_the_wrong_owner_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    secret_of_first = state.secrets[players[0]]

    # Swap owners between the first player's secret cell and one plain cell of the
    # second player. Simply moving the secret across would unbalance the per-player
    # counts, so the balance guard would fire first and this test would never reach
    # the guard it is named for.
    secret_index = _index_of_secret(plan, secret_of_first)
    plain_index = next(
        i
        for i, c in enumerate(plan.cells)
        if c.owner == players[1] and c.category not in state.secrets.values()
    )
    cells = list(plan.cells)
    cells[secret_index] = replace(cells[secret_index], owner=players[1])
    cells[plain_index] = replace(cells[plain_index], owner=players[0])

    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_SECRET_MISPLACED


def test_deal_with_a_secret_already_revealed_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    cells = list(plan.cells)
    secret_index = _index_of_secret(plan, state.secrets[players[0]])
    cells[secret_index] = replace(cells[secret_index], revealed=True)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_SECRET_REVEALED


def test_deal_with_a_plain_category_left_unrevealed_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    plain = _plain_indices(plan, set(state.secrets.values()))
    cells = list(plan.cells)
    cells[plain[0]] = replace(cells[plain[0]], revealed=False)
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_CATEGORY_NOT_REVEALED


def test_deal_that_leaves_a_secret_off_the_board_is_rejected() -> None:
    """A declared secret replaced by a plain category: no other guard notices."""
    state, players = build_setup_state(4)
    plan = _valid_plan(state, players)
    cells = list(plan.cells)
    secret_index = _index_of_secret(plan, state.secrets[players[0]])
    cells[secret_index] = replace(
        cells[secret_index], category=CategoryId(uuid4()), revealed=True
    )
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=tuple(cells)))
    assert excinfo.value.reason is RejectionReason.DEAL_SECRET_ABSENT


def test_deal_before_every_secret_is_assigned_is_rejected() -> None:
    board = BoardSize(4, 6)
    state = create_initial_state(MatchId(uuid4()), board, MatchSettings())
    state = apply(state, CreateMatch(board=board, settings=MatchSettings(), player_count=4))
    players = tuple(PlayerId(uuid4()) for _ in range(4))
    for index, player_id in enumerate(players):
        state = apply(state, AddPlayer(player_id=player_id, name=f"P{index}", colour="#fff"))
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=()))
    assert excinfo.value.reason is RejectionReason.SECRET_MISSING
