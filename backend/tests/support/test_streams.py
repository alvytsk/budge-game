"""The stream builder is test infrastructure, so it gets its own tests.

Without the coverage assertion, an event type could quietly stop appearing
in the stream and every codec, store and projection test built on it would
keep passing while covering one type less.
"""

from typing import get_args

from podvinsya.domain.events import Event
from podvinsya.domain.state import MatchStatus
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
    relies on that identity to line the log's `seq` up with the state's."""
    recorded = build_rich_stream()
    assert recorded.state.seq == len(recorded.events)


def test_the_stream_is_deterministic_in_shape() -> None:
    """Identifiers are fresh per call, but the sequence of event types is
    not allowed to wander — a flapping shape would make the golden payload
    file in Task 5 unmaintainable."""
    first = [type(event).__name__ for event in build_rich_stream().events]
    second = [type(event).__name__ for event in build_rich_stream().events]
    assert first == second
