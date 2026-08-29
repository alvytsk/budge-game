"""§A: reset returns the match to its beginning, without erasing the log.

This is a new event appended to the log, not a deletion of old ones: what
was played stays played. It is the state that gets reset, not the memory
of it.
"""

from dataclasses import replace
from uuid import uuid4

from budge.domain.actions import AddPlayer, AssignSecret, DealBoard, ResetMatch, StartMatch
from budge.domain.events import MatchReset
from budge.domain.ids import CategoryId, PlayerId
from budge.domain.state import MatchState, MatchStatus

from .conftest import apply, build_dealt_state, build_duel_state, build_running_state


def test_a_full_reset_gives_back_the_state_a_match_is_created_in(
    created_state: MatchState,
) -> None:
    """§A.3: what survives is `board`, `settings` and `player_count` --
    exactly what `CreateMatch` set -- and nothing else.

    Kills on: a reset that forgets to zero out any field of the match.
    Field by field, not by a list of known ones: a new field on
    `MatchState` that the reset doesn't know about fails this test too.
    Built from a state where `turn_order`, `turn_index`, `round_no`,
    `played_categories` and `winner` have all been pushed away from the
    values `CreateMatch` leaves them at -- `DealBoard` alone leaves those
    five untouched, so without fabricating this state, dropping any one of
    them from the `MatchReset` branch of `evolve` would still pass.
    """
    state, players = build_dealt_state(4)
    state = replace(
        state,
        turn_order=players,
        turn_index=2,
        round_no=5,
        played_categories=frozenset({CategoryId(uuid4())}),
        winner=players[0],
    )
    reset = apply(state, ResetMatch(keep_roster=False))
    assert replace(reset, seq=0) == replace(created_state, seq=0, id=reset.id)


def test_a_full_reset_drops_the_roster_and_the_secrets() -> None:
    state, _ = build_dealt_state(4)
    reset = apply(state, ResetMatch(keep_roster=False))
    assert reset.players == ()
    assert dict(reset.secrets) == {}
    assert reset.status is MatchStatus.SETUP
    assert dict(reset.groups) == {}


def test_a_reset_that_keeps_the_roster_keeps_players_and_their_secrets() -> None:
    state, players = build_dealt_state(4)
    secrets_before = dict(state.secrets)
    reset = apply(state, ResetMatch(keep_roster=True))
    assert tuple(p.id for p in reset.players) == players
    assert dict(reset.secrets) == secrets_before
    assert dict(reset.groups) == {}
    assert reset.status is MatchStatus.SETUP


def test_a_kept_roster_comes_back_with_nobody_eliminated() -> None:
    """`active_players()` filters on this flag. A roster kept along with
    its elimination marks would produce a match that starts with players
    already eliminated, and falls apart on the very first `next_turn`.

    Kills on: `players` carried over as-is, without clearing `eliminated`.
    """
    state, players = build_running_state(4)
    state = replace(
        state,
        players=tuple(
            replace(person, eliminated=True) if person.id == players[0] else person
            for person in state.players
        ),
    )
    reset = apply(state, ResetMatch(keep_roster=True))
    assert [person.eliminated for person in reset.players] == [False] * 4


def test_a_reset_from_the_middle_of_a_duel_leaves_no_duel() -> None:
    state, _, _, _ = build_duel_state()
    assert state.duel is not None
    reset = apply(state, ResetMatch(keep_roster=True))
    assert reset.duel is None
    assert reset.status is MatchStatus.SETUP


def test_a_kept_roster_can_be_dealt_and_started_again() -> None:
    """What the button exists for: "this same match again"."""
    from support.streams import make_deal

    state, players = build_running_state(4)
    reset = apply(state, ResetMatch(keep_roster=True))
    deal = make_deal(reset.board, players, dict(reset.secrets))
    dealt = apply(reset, DealBoard(), deal=deal)
    started = apply(dealt, StartMatch())
    assert started.status is MatchStatus.RUNNING
    assert len(started.groups) == started.board.cell_count


def test_resetting_a_match_that_is_already_at_the_beginning_writes_nothing(
    created_state: MatchState,
) -> None:
    """§A.4, by the precedent of `AssignSecret`: an operator who presses
    "Reset" twice should not get an error for having gotten what they
    wanted.

    Kills on: an unconditional `MatchReset` -- the log would grow by one
    event per press, and the API would never see a noop returned.
    """
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    events = decide(created_state, ResetMatch(keep_roster=False), DecisionContext(now=BASE_TIME))
    assert events == ()


def test_keeping_the_roster_of_an_untouched_setup_writes_nothing(
    created_state: MatchState,
) -> None:
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    state = apply(
        created_state,
        AddPlayer(player_id=PlayerId(uuid4()), name="Аня", colour="#e4572e"),
    )
    events = decide(state, ResetMatch(keep_roster=True), DecisionContext(now=BASE_TIME))
    assert events == ()


def test_a_full_reset_of_a_setup_with_a_roster_does_write(
    created_state: MatchState,
) -> None:
    """Same SETUP, same roster -- but a different flag, and now there is
    something to erase."""
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    player_id = PlayerId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Аня", colour="#e4572e"))
    state = apply(state, AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    events = decide(state, ResetMatch(keep_roster=False), DecisionContext(now=BASE_TIME))
    assert events == (MatchReset(keep_roster=False),)


def test_reset_is_legal_in_every_status() -> None:
    """§A.4. Checked mid-match, not only after it."""
    for build in (build_dealt_state, build_running_state):
        state, _ = build(4)
        assert apply(state, ResetMatch(keep_roster=True)).status is MatchStatus.SETUP
