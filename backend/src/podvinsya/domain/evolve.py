from collections.abc import Iterable
from dataclasses import replace

from podvinsya.domain.events import BoardDealt, Event, MatchCreated, PlayerAdded, SecretAssigned
from podvinsya.domain.state import Group, MatchState, Player


def evolve(state: MatchState, event: Event) -> MatchState:
    match event:
        case MatchCreated():
            evolved = replace(
                state,
                board=event.board,
                settings=event.settings,
                player_count=event.player_count,
            )
        case PlayerAdded():
            evolved = replace(
                state,
                players=(
                    *state.players,
                    Player(event.player_id, event.name, event.colour),
                ),
            )
        case SecretAssigned():
            evolved = replace(
                state,
                secrets={**state.secrets, event.player_id: event.category},
            )
        case BoardDealt():
            evolved = replace(
                state,
                groups={
                    dealt.group_id: Group(
                        id=dealt.group_id,
                        owner=dealt.owner,
                        category=dealt.category,
                        cells=frozenset({dealt.cell}),
                        revealed=dealt.revealed,
                    )
                    for dealt in event.cells
                },
                played_categories=frozenset(),
            )
        case _:
            raise NotImplementedError(type(event).__name__)
    return replace(evolved, seq=state.seq + 1)


def fold(state: MatchState, events: Iterable[Event]) -> MatchState:
    for event in events:
        state = evolve(state, event)
    return state
