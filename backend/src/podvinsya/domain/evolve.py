from collections.abc import Iterable
from dataclasses import replace

from podvinsya.domain.events import Event, MatchCreated
from podvinsya.domain.state import MatchState


def evolve(state: MatchState, event: Event) -> MatchState:
    match event:
        case MatchCreated():
            evolved = replace(
                state,
                board=event.board,
                settings=event.settings,
                player_count=event.player_count,
            )
        case _:
            raise NotImplementedError(type(event).__name__)
    return replace(evolved, seq=state.seq + 1)


def fold(state: MatchState, events: Iterable[Event]) -> MatchState:
    for event in events:
        state = evolve(state, event)
    return state
