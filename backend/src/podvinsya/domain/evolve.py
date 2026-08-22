from collections.abc import Iterable
from dataclasses import replace

from podvinsya.domain.events import (
    AttackDeclared,
    BoardDealt,
    DuelStarted,
    Event,
    MatchCreated,
    MatchStarted,
    PlayerAdded,
    SecretAssigned,
)
from podvinsya.domain.state import Duel, DuelPhase, Group, MatchState, MatchStatus, Player


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
        case MatchStarted():
            evolved = replace(
                state,
                status=MatchStatus.RUNNING,
                turn_order=event.turn_order,
                turn_index=0,
                round_no=1,
            )
        case AttackDeclared():
            defending = state.groups[event.defending_group]
            evolved = replace(
                state,
                groups={**state.groups, defending.id: replace(defending, revealed=True)},
                duel=Duel(
                    attacker=event.attacker,
                    defender=event.defender,
                    attacking_group=event.attacking_group,
                    defending_group=event.defending_group,
                    category=event.category,
                    image_order=event.image_order,
                    index=0,
                    answering=event.attacker,
                    budgets=event.budgets,
                    anchor=None,
                    phase=DuelPhase.DECLARED,
                ),
            )
        case DuelStarted():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(duel, anchor=event.anchor, phase=DuelPhase.RUNNING),
            )
        case _:
            raise NotImplementedError(type(event).__name__)
    return replace(evolved, seq=state.seq + 1)


def _duel(state: MatchState) -> Duel:
    if state.duel is None:
        raise NotImplementedError("event requires an active duel")
    return state.duel


def fold(state: MatchState, events: Iterable[Event]) -> MatchState:
    for event in events:
        state = evolve(state, event)
    return state
