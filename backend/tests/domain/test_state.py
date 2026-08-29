from uuid import uuid4

from budge.domain.board import BoardSize
from budge.domain.budgets import Budgets
from budge.domain.genesis import create_initial_state
from budge.domain.ids import MatchId, PlayerId
from budge.domain.settings import MatchSettings
from budge.domain.state import MatchStatus


def test_default_settings_match_the_spec() -> None:
    settings = MatchSettings()
    assert settings.base_seconds == 60
    assert settings.bonus_cap_seconds == 15
    assert settings.pass_penalty_seconds == 3


def test_budgets_are_immutable_values() -> None:
    alice, bob = PlayerId(uuid4()), PlayerId(uuid4())
    budgets = Budgets.of({alice: 60_000, bob: 60_000})
    charged = budgets.charge(alice, 4_200)
    assert budgets.get(alice) == 60_000, "original must not be mutated"
    assert charged.get(alice) == 55_800
    assert charged.get(bob) == 60_000
    assert set(charged.players()) == {alice, bob}


def test_charging_below_zero_clamps_to_zero() -> None:
    alice = PlayerId(uuid4())
    budgets = Budgets.of({alice: 2_000})
    assert budgets.charge(alice, 5_000).get(alice) == 0


def test_genesis_produces_an_empty_setup_match() -> None:
    match_id = MatchId(uuid4())
    board = BoardSize(width=4, height=6)
    state = create_initial_state(match_id, board, MatchSettings())

    assert state.id == match_id
    assert state.seq == 0
    assert state.status is MatchStatus.SETUP
    assert state.board == board
    assert state.players == ()
    assert state.groups == {}
    assert state.duel is None
    assert state.winner is None
    assert state.played_categories == frozenset()
