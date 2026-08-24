"""§A: сброс возвращает партию в начало, не стирая лог.

Это новое событие в append-only логе, а не удаление старых: то, что было
сыграно, остаётся сыгранным. Сбрасывается состояние, не память.
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
    """§A.3: остаются `board`, `settings` и `player_count` — ровно то, что
    задал `CreateMatch`, — и больше ничего.

    Kills on: сброс, забывающий обнулить любое из полей партии. Поле в
    поле, а не по списку известных: новое поле в `MatchState`, о котором
    сброс не узнал, валит этот тест.
    """
    state, _ = build_dealt_state(4)
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
    """`active_players()` фильтрует по этому флагу. Ростер, сохранённый
    вместе с отметками о выбывании, дал бы партию, которая начинается с уже
    выбывшими игроками и рассыпается на первом же `next_turn`.

    Kills on: `players` перенесённый как есть, без снятия `eliminated`.
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
    """Ради чего кнопка и существует: «эту же партию ещё раз»."""
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
    """§A.4, по прецеденту `AssignSecret`: оператор, дважды нажавший
    «Сбросить», не должен получать ошибку за то, что добился желаемого.

    Kills on: безусловный `MatchReset` — лог рос бы на событие за каждое
    нажатие, и `noop` в API никогда бы не возвращался.
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
    """Тот же SETUP, тот же ростер — но флаг другой, и стирать есть что."""
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide

    from .conftest import BASE_TIME

    player_id = PlayerId(uuid4())
    state = apply(created_state, AddPlayer(player_id=player_id, name="Аня", colour="#e4572e"))
    state = apply(state, AssignSecret(player_id=player_id, category=CategoryId(uuid4())))
    events = decide(state, ResetMatch(keep_roster=False), DecisionContext(now=BASE_TIME))
    assert events == (MatchReset(keep_roster=False),)


def test_reset_is_legal_in_every_status() -> None:
    """§A.4. Механика проверяется в середине партии, а не после неё."""
    for build in (build_dealt_state, build_running_state):
        state, _ = build(4)
        assert apply(state, ResetMatch(keep_roster=True)).status is MatchStatus.SETUP
