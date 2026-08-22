from collections.abc import Iterable
from dataclasses import replace

from podvinsya.domain.events import (
    AnswerAccepted,
    AttackDeclared,
    BoardDealt,
    DuelPaused,
    DuelResolved,
    DuelResumed,
    DuelStarted,
    Event,
    MatchCreated,
    MatchStarted,
    PassUsed,
    PlayerAdded,
    SecretAssigned,
)
from podvinsya.domain.rules import next_turn
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
        case AnswerAccepted():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=duel.budgets.charge(event.player, event.charged_ms),
                    answering=event.next_answering,
                    index=duel.index + 1,
                    anchor=event.anchor,
                ),
            )
        case PassUsed():
            duel = _duel(state)
            charged = duel.budgets.charge(event.player, event.charged_ms)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=charged.charge(event.player, event.penalty_ms),
                    index=duel.index + 1,
                    anchor=event.anchor,
                ),
            )
        case DuelPaused():
            duel = _duel(state)
            evolved = replace(
                state,
                duel=replace(
                    duel,
                    budgets=duel.budgets.charge(duel.answering, event.charged_ms),
                    anchor=None,
                ),
            )
        case DuelResumed():
            duel = _duel(state)
            evolved = replace(state, duel=replace(duel, anchor=event.anchor))
        case DuelResolved():
            surviving = state.groups[event.surviving_group]
            merged = replace(
                surviving,
                owner=event.winner,
                cells=surviving.cells | event.absorbed_cells,
            )
            groups = {
                gid: g for gid, g in state.groups.items() if gid != event.absorbed_group
            }
            groups[merged.id] = merged
            after = replace(
                state,
                groups=groups,
                played_categories=state.played_categories | {event.burned_category},
                duel=None,
            )
            turn_index, round_no = next_turn(after)
            evolved = replace(after, turn_index=turn_index, round_no=round_no)
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
