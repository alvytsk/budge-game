from dataclasses import replace

from podvinsya.domain.actions import ExpireTimer
from podvinsya.domain.state import MatchStatus

from .conftest import apply, at, build_duel_state


def _leave_only(state, player, keep_group_id):  # type: ignore[no-untyped-def]
    """Strip a player down to a single group so the next loss eliminates them."""
    groups = {
        gid: g for gid, g in state.groups.items()
        if g.owner != player or gid == keep_group_id
    }
    other = next(g.owner for g in groups.values() if g.owner != player)
    for gid, g in list(state.groups.items()):
        if g.owner == player and gid != keep_group_id:
            groups[gid] = replace(g, owner=other)
    return replace(state, groups=groups)


def test_losing_your_last_group_eliminates_you() -> None:
    state, _, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    state = _leave_only(state, duel.defender, defending)
    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = replace(state, duel=aimed)

    state = apply(state, ExpireTimer(deadline_id=0), now=at(2))

    assert state.player(duel.defender).eliminated is True
    assert state.groups_of(duel.defender) == ()


def test_a_player_with_groups_left_is_not_eliminated() -> None:
    state, _, _, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = apply(replace(state, duel=aimed), ExpireTimer(deadline_id=0), now=at(2))
    assert state.player(duel.defender).eliminated is False


def test_the_last_player_standing_wins_and_the_match_finishes() -> None:
    state, players, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    bystanders = [p for p in players if p not in (duel.attacker, duel.defender)]

    groups = dict(state.groups)
    for gid, group in list(groups.items()):
        if group.owner in bystanders:
            groups[gid] = replace(group, owner=duel.attacker)
    players_tuple = tuple(
        replace(p, eliminated=p.id in bystanders) for p in state.players
    )
    state = replace(state, groups=groups, players=players_tuple)
    state = _leave_only(state, duel.defender, defending)

    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = apply(replace(state, duel=aimed), ExpireTimer(deadline_id=0), now=at(2))

    assert state.status is MatchStatus.FINISHED
    assert state.winner == duel.attacker
    assert {g.owner for g in state.groups.values()} == {duel.attacker}


def test_a_finished_match_refuses_further_attacks() -> None:
    import pytest

    from podvinsya.domain.actions import DeclareAttack
    from podvinsya.domain.errors import Rejected, RejectionReason

    state, players, attacking, defending = build_duel_state()
    duel = state.duel
    assert duel is not None
    bystanders = [p for p in players if p not in (duel.attacker, duel.defender)]
    groups = {
        gid: (replace(g, owner=duel.attacker) if g.owner in bystanders else g)
        for gid, g in state.groups.items()
    }
    players_tuple = tuple(replace(p, eliminated=p.id in bystanders) for p in state.players)
    state = _leave_only(
        replace(state, groups=groups, players=players_tuple), duel.defender, defending
    )
    aimed = replace(duel, answering=duel.defender,
                    budgets=duel.budgets.with_value(duel.defender, 1_000))
    state = apply(replace(state, duel=aimed), ExpireTimer(deadline_id=0), now=at(2))

    with pytest.raises(Rejected) as excinfo:
        apply(state, DeclareAttack(attacking_group=attacking, defending_group=attacking))
    assert excinfo.value.reason is RejectionReason.WRONG_STATUS
