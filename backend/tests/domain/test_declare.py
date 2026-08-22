from uuid import uuid4

import pytest

from podvinsya.domain.actions import DeclareAttack
from podvinsya.domain.errors import Rejected, RejectionReason
from podvinsya.domain.ids import GroupId
from podvinsya.domain.rules import legal_targets, starting_budget_ms
from podvinsya.domain.state import DuelPhase

from .conftest import IMAGE_POOL, apply, build_declared_state, build_running_state


def test_declaring_creates_a_declared_duel_without_starting_the_clock() -> None:
    state, _, attacking, defending = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.phase is DuelPhase.DECLARED
    assert duel.anchor is None
    assert duel.index == 0
    assert duel.attacking_group == attacking
    assert duel.defending_group == defending


def test_the_defender_category_is_played_and_the_attacker_answers_first() -> None:
    state, _, attacking, defending = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.category == state.groups[defending].category
    assert duel.answering == duel.attacker
    assert duel.attacker == state.groups[attacking].owner
    assert duel.defender == state.groups[defending].owner


def test_declaring_reveals_the_defending_group_only() -> None:
    state, players = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(
        g for g in state.groups.values() if g.owner == attacker_id and not g.revealed
    )
    targets = legal_targets(state, attacking.id)
    defending_id = sorted(targets)[0]
    before_attacker_revealed = attacking.revealed

    state = apply(
        state,
        DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
        image_order=IMAGE_POOL,
    )

    assert state.groups[defending_id].revealed is True
    assert state.groups[attacking.id].revealed is before_attacker_revealed is False


def test_budgets_come_from_each_side_own_group() -> None:
    state, _, attacking, defending = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(duel.attacker) == starting_budget_ms(
        state.groups[attacking], state.settings
    )
    assert duel.budgets.get(duel.defender) == starting_budget_ms(
        state.groups[defending], state.settings
    )


def test_budgets_are_not_swapped_between_the_sides() -> None:
    from dataclasses import replace

    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(
        g for g in state.groups.values() if g.owner == attacker_id and legal_targets(state, g.id)
    )
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    defending = state.groups[defending_id]

    # Grow the attacking group so the two sides carry different bonuses. Every group is
    # a single cell at declaration time, so both bonuses are zero and a swapped wiring
    # is invisible — this is the only test that can see the difference. The grown group
    # is deliberately not connected: nothing validates connectivity at declaration, and
    # the Latin-square deal leaves a player no same-owner orthogonal neighbour to absorb.
    donor = next(
        g
        for g in state.groups.values()
        if g.owner == attacker_id and g.id not in (attacking.id, defending_id)
    )
    grown = replace(attacking, cells=attacking.cells | donor.cells)
    groups = {gid: g for gid, g in state.groups.items() if gid != donor.id}
    groups[grown.id] = grown
    state = replace(state, groups=groups)

    state = apply(
        state,
        DeclareAttack(attacking_group=grown.id, defending_group=defending_id),
        image_order=IMAGE_POOL,
    )

    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(duel.attacker) == starting_budget_ms(grown, state.settings)
    assert duel.budgets.get(duel.defender) == starting_budget_ms(defending, state.settings)
    assert duel.budgets.get(duel.attacker) != duel.budgets.get(duel.defender), (
        "the sides must differ here, or this test cannot see a swap"
    )


def test_the_whole_image_order_is_drawn_up_front() -> None:
    state, _, _, _ = build_declared_state()
    duel = state.duel
    assert duel is not None
    assert duel.image_order == IMAGE_POOL
    assert len(set(duel.image_order)) == len(duel.image_order)


def test_attacking_out_of_turn_is_rejected() -> None:
    state, players = build_running_state(4)
    current = state.current_player()
    other = next(p for p in players if p != current)

    # The defending group deliberately belongs to the CURRENT player. A guard that
    # compared the defender's owner to the current player — rather than the attacker's —
    # would let this declaration through, so this pairing is what gives the test the
    # power to see that mutation. Any enemy target would satisfy the assertion.
    attacking, defending_id = next(
        (g, target)
        for g in state.groups.values()
        if g.owner == other
        for target in sorted(legal_targets(state, g.id))
        if state.groups[target].owner == current
    )

    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            DeclareAttack(attacking_group=attacking.id, defending_group=defending_id),
            image_order=IMAGE_POOL,
        )
    assert excinfo.value.reason is RejectionReason.NOT_YOUR_TURN


def test_attacking_your_own_group_is_rejected() -> None:
    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    own = [g for g in state.groups.values() if g.owner == attacker_id]
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            DeclareAttack(attacking_group=own[0].id, defending_group=own[1].id),
            image_order=IMAGE_POOL,
        )
    assert excinfo.value.reason is RejectionReason.TARGET_IS_YOURS


def test_attacking_a_non_adjacent_group_is_rejected() -> None:
    from podvinsya.domain.board import groups_are_adjacent

    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(g for g in state.groups.values() if g.owner == attacker_id)
    far = next(
        g
        for g in state.groups.values()
        if g.owner != attacker_id
        and not groups_are_adjacent(attacking.cells, g.cells, state.board)
    )
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            DeclareAttack(attacking_group=attacking.id, defending_group=far.id),
            image_order=IMAGE_POOL,
        )
    assert excinfo.value.reason is RejectionReason.NOT_ADJACENT


def test_unknown_group_is_rejected() -> None:
    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(g for g in state.groups.values() if g.owner == attacker_id)
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            DeclareAttack(attacking_group=attacking.id, defending_group=GroupId(uuid4())),
            image_order=IMAGE_POOL,
        )
    assert excinfo.value.reason is RejectionReason.UNKNOWN_GROUP


def test_declaring_while_a_duel_exists_is_rejected() -> None:
    state, _, attacking, defending = build_declared_state()
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            DeclareAttack(attacking_group=attacking, defending_group=defending),
            image_order=IMAGE_POOL,
        )
    assert excinfo.value.reason is RejectionReason.DUEL_IN_PROGRESS


def test_declaring_without_an_image_order_is_rejected() -> None:
    state, _ = build_running_state(4)
    attacker_id = state.current_player()
    attacking = next(
        g
        for g in state.groups.values()
        if g.owner == attacker_id and legal_targets(state, g.id)
    )
    defending_id = sorted(legal_targets(state, attacking.id))[0]
    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking.id, defending_group=defending_id))
    assert excinfo.value.reason is RejectionReason.IMAGES_EXHAUSTED
