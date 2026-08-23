"""The frozen wire-name registry.

`WIRE_NAMES` maps each `Event` union member to the string stored in
`match_events.type`. It is a module-level literal — not derived from
`cls.__name__` — because the wire name and the Python class name are allowed
to diverge: a class can be renamed in a refactor without that being a data
migration, precisely because nothing here reads `__name__`. Changing a
*value* in this dict, on the other hand, is a data migration over
`match_events.type`.

`CURRENT_VERSION` gives every registered wire type a starting schema version
of 1. It is derived from `WIRE_NAMES`' keys — themselves a literal — so
every registered event is guaranteed an entry and none can be forgotten.
"""

from collections.abc import Mapping
from typing import Any

from budge.domain.events import (
    AnswerAccepted,
    AttackDeclared,
    BoardDealt,
    DuelPaused,
    DuelResolved,
    DuelResumed,
    DuelStarted,
    JudgementUndone,
    MatchCreated,
    MatchStarted,
    MatchWon,
    PassUsed,
    PlayerAdded,
    PlayerEliminated,
    SecretAssigned,
)

WIRE_NAMES: Mapping[type[Any], str] = {
    MatchCreated: "match.created",
    PlayerAdded: "match.player_added",
    SecretAssigned: "match.secret_assigned",
    BoardDealt: "match.board_dealt",
    MatchStarted: "match.started",
    PlayerEliminated: "match.player_eliminated",
    MatchWon: "match.won",
    AttackDeclared: "duel.attack_declared",
    DuelStarted: "duel.started",
    AnswerAccepted: "duel.answer_accepted",
    PassUsed: "duel.pass_used",
    DuelPaused: "duel.paused",
    DuelResumed: "duel.resumed",
    JudgementUndone: "duel.judgement_undone",
    DuelResolved: "duel.resolved",
}

CLASSES_BY_WIRE_NAME: Mapping[str, type[Any]] = {name: cls for cls, name in WIRE_NAMES.items()}

CURRENT_VERSION: Mapping[str, int] = dict.fromkeys(WIRE_NAMES.values(), 1)
