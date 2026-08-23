"""The stream builder is test infrastructure, so it gets its own tests.

Without the coverage assertion, an event type could quietly stop appearing
in the stream and every codec, store and projection test built on it would
keep passing while covering one type less.
"""

from collections import Counter
from typing import get_args

from budge.domain.events import Event
from budge.domain.state import MatchStatus
from support.streams import build_rich_stream


def test_the_rich_stream_contains_every_event_type() -> None:
    recorded = build_rich_stream()
    produced = {type(event) for event in recorded.events}
    assert produced == set(get_args(Event)), (
        "every later task's coverage depends on this stream exercising the "
        f"whole union; missing: {set(get_args(Event)) - produced}"
    )


def test_the_rich_stream_ends_in_a_won_match() -> None:
    recorded = build_rich_stream()
    assert recorded.state.status is MatchStatus.FINISHED
    assert recorded.state.winner is not None
    assert len(recorded.state.active_players()) == 1


def test_the_stream_is_a_foldable_log() -> None:
    """seq counts events, one per event, starting at one. Every later task
    relies on that identity to line the log's `seq` up with the state's.

    This only guards that `_Recorder.apply`'s own bookkeeping is internally
    consistent -- its `self.events.extend(produced)` and
    `self.state = fold(self.state, produced)` agree on how many events were
    produced. It does not guard that `build_rich_stream` assembles the right
    *sequence* of commands: a duplicated or dropped command changes both
    sides of this equation together and slips right past it.
    `test_the_stream_has_the_expected_shape` below is what catches that.
    """
    recorded = build_rich_stream()
    assert recorded.state.seq == len(recorded.events)


def test_the_stream_has_the_expected_shape() -> None:
    """Pins down the exact command sequence `build_rich_stream` assembles,
    not just the state/log bookkeeping that `test_the_stream_is_a_foldable_log`
    checks.

    A duplicated or dropped judging command changes the per-type counts
    below (and often the total), so this catches an authoring slip that a
    `seq == len(events)` check cannot: fold's two lines move together even
    when the *command sequence* itself is wrong. A reordering of the
    hand-played first duel -- e.g. undo before pause -- changes the ordered
    prefix asserted below, from `MatchCreated` through the first
    `DuelResolved`.

    All values here were read off a real run of the builder, not guessed:
    counts and prefix were printed directly from `build_rich_stream()`'s
    output. `AnswerAccepted`, `PassUsed`, `DuelPaused`, `DuelResumed` and
    `JudgementUndone` are each 1 because only the hand-played first duel
    exercises judging, passing, pausing, resuming and undoing -- every
    later duel is decided by the clock via `ExpireTimer`. `AttackDeclared`,
    `DuelStarted` and `DuelResolved` are each 10, one per duel: the match
    takes ten duels to finish (see `build_rich_stream`'s docstring for why
    that is fewer than the eleven-duel upper bound). `PlayerEliminated` and
    `MatchWon` are each 1 because this is a two-player match: the first
    (and only) elimination ends it.
    """
    recorded = build_rich_stream()
    names = [type(event).__name__ for event in recorded.events]

    expected_counts = {
        "MatchCreated": 1,
        "PlayerAdded": 2,
        "SecretAssigned": 2,
        "BoardDealt": 1,
        "MatchStarted": 1,
        "AttackDeclared": 10,
        "DuelStarted": 10,
        "AnswerAccepted": 1,
        "PassUsed": 1,
        "DuelPaused": 1,
        "DuelResumed": 1,
        "JudgementUndone": 1,
        "DuelResolved": 10,
        "PlayerEliminated": 1,
        "MatchWon": 1,
    }
    assert Counter(names) == Counter(expected_counts)
    assert len(names) == sum(expected_counts.values()) == 44

    expected_first_duel_prefix = [
        "MatchCreated",
        "PlayerAdded",
        "SecretAssigned",
        "PlayerAdded",
        "SecretAssigned",
        "BoardDealt",
        "MatchStarted",
        "AttackDeclared",
        "DuelStarted",
        "AnswerAccepted",
        "PassUsed",
        "DuelPaused",
        "DuelResumed",
        "JudgementUndone",
        "DuelResolved",
    ]
    first_resolved = names.index("DuelResolved")
    assert names[: first_resolved + 1] == expected_first_duel_prefix


def test_the_stream_is_deterministic_in_shape() -> None:
    """Identifiers are fresh per call, but the sequence of event types is
    not allowed to wander — a flapping shape would make the golden payload
    file in Task 5 unmaintainable."""
    first = [type(event).__name__ for event in build_rich_stream().events]
    second = [type(event).__name__ for event in build_rich_stream().events]
    assert first == second
