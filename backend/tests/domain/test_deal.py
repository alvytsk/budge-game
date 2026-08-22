from collections import Counter
from dataclasses import replace
from uuid import uuid4

import pytest

from podvinsya.domain.actions import DealBoard
from podvinsya.domain.context import DealPlan, DealtCell
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.ids import GroupId

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


def test_deal_missing_a_cell_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = make_deal(state.board, players, dict(state.secrets))
    truncated = DealPlan(cells=plan.cells[:-1])
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=truncated)
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_with_a_duplicate_category_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = make_deal(state.board, players, dict(state.secrets))
    clashing = DealPlan(
        cells=(
            *plan.cells[:-1],
            DealtCell(
                cell=plan.cells[-1].cell,
                owner=plan.cells[-1].owner,
                category=plan.cells[0].category,
                group_id=GroupId(uuid4()),
                revealed=True,
            ),
        )
    )
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=clashing)
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_with_a_secret_on_the_wrong_owner_is_rejected() -> None:
    state, players = build_setup_state(4)
    plan = make_deal(state.board, players, dict(state.secrets))
    secret_of_first = state.secrets[players[0]]

    # Swap owners between the first player's secret cell and one plain cell of the
    # second player. Simply moving the secret across would unbalance the per-player
    # counts and trip the earlier balance guard, leaving the secret-ownership guard
    # untested while the test still passed on the same DEAL_INVALID reason.
    secret_index = next(i for i, c in enumerate(plan.cells) if c.category == secret_of_first)
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
    assert excinfo.value.reason is RejectionReason.DEAL_INVALID


def test_deal_before_every_secret_is_assigned_is_rejected() -> None:
    from podvinsya.domain.actions import AddPlayer, CreateMatch
    from podvinsya.domain.board import BoardSize
    from podvinsya.domain.genesis import create_initial_state
    from podvinsya.domain.ids import MatchId, PlayerId
    from podvinsya.domain.settings import MatchSettings

    board = BoardSize(4, 6)
    state = create_initial_state(MatchId(uuid4()), board, MatchSettings())
    state = apply(state, CreateMatch(board=board, settings=MatchSettings(), player_count=4))
    players = tuple(PlayerId(uuid4()) for _ in range(4))
    for index, player_id in enumerate(players):
        state = apply(state, AddPlayer(player_id=player_id, name=f"P{index}", colour="#fff"))
    with pytest.raises(Rejected) as excinfo:
        apply(state, DealBoard(), deal=DealPlan(cells=()))
    assert excinfo.value.reason is RejectionReason.SECRET_MISSING
