from uuid import uuid4

import pytest

from budge.domain.actions import AddPlayer, AssignSecret
from budge.domain.context import DecisionContext
from budge.domain.decide import decide
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.ids import CategoryId, PlayerId
from budge.domain.state import MatchState

from .conftest import BASE_TIME, apply, build_setup_state


def test_adding_a_player_records_name_and_colour(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    state = apply(
        created_state,
        AddPlayer(player_id=player_id, name="Кира", colour="#a855f7"),
    )
    assert len(state.players) == 1
    player = state.player(player_id)
    assert player.name == "Кира"
    assert player.colour == "#a855f7"
    assert player.eliminated is False


def test_adding_the_same_player_twice_is_rejected(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    state = apply(
        created_state,
        AddPlayer(player_id=player_id, name="Кира", colour="#a855f7"),
    )
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            AddPlayer(player_id=player_id, name="Кира", colour="#3b82f6"),
        )
    assert excinfo.value.reason is RejectionReason.DUPLICATE_PLAYER


def test_adding_more_players_than_declared_is_rejected(
    created_state: MatchState,
) -> None:
    state = created_state
    for index in range(4):
        state = apply(
            state,
            AddPlayer(
                player_id=PlayerId(uuid4()),
                name=f"P{index}",
                colour="#fff",
            ),
        )
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            AddPlayer(
                player_id=PlayerId(uuid4()),
                name="fifth",
                colour="#000",
            ),
        )
    assert excinfo.value.reason is RejectionReason.PLAYER_COUNT_INVALID


def test_secret_is_bound_to_its_owner(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = apply(
        created_state,
        AddPlayer(player_id=player_id, name="Дед", colour="#22c55e"),
    )
    state = apply(
        state,
        AssignSecret(player_id=player_id, category=category),
    )
    assert state.secrets[player_id] == category


def test_secret_for_unknown_player_is_rejected(created_state: MatchState) -> None:
    with pytest.raises(Rejected) as excinfo:
        apply(
            created_state,
            AssignSecret(
                player_id=PlayerId(uuid4()),
                category=CategoryId(uuid4()),
            ),
        )
    assert excinfo.value.reason is RejectionReason.UNKNOWN_PLAYER


def test_reassigning_a_secret_replaces_it(created_state: MatchState) -> None:
    player_id = PlayerId(uuid4())
    first, second = CategoryId(uuid4()), CategoryId(uuid4())
    state = apply(
        created_state,
        AddPlayer(player_id=player_id, name="Дед", colour="#22c55e"),
    )
    state = apply(
        state,
        AssignSecret(player_id=player_id, category=first),
    )
    state = apply(
        state,
        AssignSecret(player_id=player_id, category=second),
    )
    assert state.secrets[player_id] == second


def test_reassigning_the_same_secret_emits_nothing(
    created_state: MatchState,
) -> None:
    player_id = PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = apply(
        created_state,
        AddPlayer(player_id=player_id, name="Дед", colour="#22c55e"),
    )
    state = apply(
        state,
        AssignSecret(player_id=player_id, category=category),
    )

    command = AssignSecret(player_id=player_id, category=category)
    assert decide(state, command, DecisionContext(now=BASE_TIME)) == (), (
        "legal but unchanged must produce no event — neither a rejection nor"
        " a duplicate"
    )

    unchanged = apply(state, command)
    assert unchanged.seq == state.seq
    assert unchanged.secrets[player_id] == category


def test_two_players_cannot_share_a_secret_category(
    created_state: MatchState,
) -> None:
    alice, bob = PlayerId(uuid4()), PlayerId(uuid4())
    category = CategoryId(uuid4())
    state = apply(
        created_state,
        AddPlayer(player_id=alice, name="A", colour="#fff"),
    )
    state = apply(
        state,
        AddPlayer(player_id=bob, name="B", colour="#000"),
    )
    state = apply(
        state,
        AssignSecret(player_id=alice, category=category),
    )
    with pytest.raises(Rejected) as excinfo:
        apply(
            state,
            AssignSecret(player_id=bob, category=category),
        )
    assert excinfo.value.reason is RejectionReason.DUPLICATE_CATEGORY


def test_setup_builder_produces_a_complete_setup() -> None:
    state, players = build_setup_state(4)
    assert len(state.players) == 4
    assert set(state.secrets) == set(players)
