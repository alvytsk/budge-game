"""What the codec owes: an exact round trip for every event the domain can
emit, and a loud refusal for anything it cannot represent."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args

import pytest

from podvinsya.db.codec import decode, encode, normalize_utc
from podvinsya.db.codec.registry import CURRENT_VERSION, WIRE_NAMES
from podvinsya.db.errors import NaiveDatetime, UnknownEventType, UnknownSchemaVersion
from podvinsya.domain.board import Cell
from podvinsya.domain.events import DuelResolved, DuelStarted, Event
from support import streams
from support.streams import build_rich_stream, deterministic_uuid4

GOLDEN = Path(__file__).parent / "golden" / "rich_stream.json"


def _is_list_of_lists(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(i, list) for i in value)


def _canonical(payload: dict[str, Any]) -> dict[str, Any]:
    """Sort any list whose items are themselves lists.

    The only field that produces one is `DuelResolved.absorbed_cells`, a
    frozenset whose dumped order is arbitrary. Nothing reads these arrays
    positionally, so sorting for comparison loses no information — see the
    note in codec.py.
    """
    return {
        key: sorted(value) if _is_list_of_lists(value) else value
        for key, value in payload.items()
    }


def test_every_event_of_a_real_match_survives_a_round_trip() -> None:
    events = build_rich_stream().events
    for event in events:
        wire_type, version, payload = encode(event)
        assert json.loads(json.dumps(payload)) == payload, "payload must be JSON-native"
        assert decode(wire_type, version, payload) == event


def test_the_round_trip_covers_the_whole_event_union() -> None:
    """Without this, the loop above could silently cover twelve of fifteen."""
    events = build_rich_stream().events
    assert {type(event) for event in events} == set(get_args(Event))


def test_encode_reports_the_registered_wire_type_and_current_version() -> None:
    event = next(e for e in build_rich_stream().events if isinstance(e, DuelStarted))
    wire_type, version, _ = encode(event)
    assert wire_type == WIRE_NAMES[DuelStarted]
    assert version == CURRENT_VERSION[wire_type]


def test_a_non_utc_offset_is_normalised_rather_than_refused() -> None:
    """An offset denotes a real instant, so rejecting it would turn a
    harmless producer difference into a match that cannot load."""
    moscow = timezone(timedelta(hours=3))
    event = DuelStarted(anchor=datetime(2026, 8, 22, 15, 0, tzinfo=moscow))
    _, _, payload = encode(event)
    assert payload["anchor"] == "2026-08-22T12:00:00Z"
    decoded = decode("duel.started", 1, payload)
    assert isinstance(decoded, DuelStarted)
    assert decoded.anchor == datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def test_a_naive_datetime_is_refused_with_its_path() -> None:
    """Pydantic round-trips a naive datetime silently. There is no correct
    instant to recover from one, so the codec refuses rather than guessing."""
    event = DuelStarted(anchor=datetime(2026, 8, 22, 12, 0))
    with pytest.raises(NaiveDatetime) as excinfo:
        encode(event)
    assert "anchor" in str(excinfo.value)


def test_a_naive_datetime_nested_in_a_payload_is_refused_on_decode() -> None:
    _, _, payload = encode(DuelStarted(anchor=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)))
    payload["anchor"] = "2026-08-22T12:00:00"
    with pytest.raises(NaiveDatetime):
        decode("duel.started", 1, payload)


def test_an_unregistered_wire_type_is_refused() -> None:
    with pytest.raises(UnknownEventType):
        decode("duel.answer_was_wrong", 1, {})


def test_a_future_schema_version_is_refused() -> None:
    """A log written by a newer deployment must not be silently misread."""
    _, _, payload = encode(DuelStarted(anchor=datetime(2026, 8, 22, 12, 0, tzinfo=UTC)))
    with pytest.raises(UnknownSchemaVersion):
        decode("duel.started", 99, payload)


def test_the_payload_shape_matches_the_golden_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The round-trip tests prove the codec is self-consistent; only a
    checked-in payload proves the shape on disk has not changed under a
    library upgrade — `Cell` rendering as `{"col": 0, "row": 0}` instead of
    `[0, 0]`, `Budgets.entries` changing container shape, a datetime
    rendering as `+00:00` instead of `Z`. Catching that needs an exact
    value comparison, not just a check that the same field names are
    present.

    A value comparison needs the checked-in file and a fresh run to agree
    on values, not just on shape, so `build_rich_stream`'s ids are pinned
    via `deterministic_uuid4` for the duration of this test. Everything
    else about the match — which duels happen, who wins, which cells get
    absorbed — is otherwise a function of those ids too (`legal_targets` is
    resolved via `sorted()` over `GroupId`, a UUID), so pinning the ids
    makes the whole stream, values included, reproducible against the file
    below. No other test should do this: fresh random ids are what let two
    streams coexist in the same database without their primary keys
    colliding.

    Both sides go through `_canonical` before comparison: `DuelResolved
    .absorbed_cells` is a frozenset, and Pydantic dumps a frozenset in
    set-iteration order, which pinning the ids does not itself pin.

    This also folds in what used to be a separate `test_the_golden_file_
    decodes`: decoding each checked-in row and comparing it back to the
    real event that produced it proves decode does not raise on the
    checked-in payload, and proves something stronger — that it
    reconstructs exactly the event that produced it.
    """
    monkeypatch.setattr(streams, "uuid4", deterministic_uuid4())
    events = build_rich_stream().events
    produced: list[dict[str, Any]] = [
        {"type": t, "schema_version": v, "payload": _canonical(p)}
        for t, v, p in (encode(event) for event in events)
    ]
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    canonical_golden = [
        {
            "type": row["type"],
            "schema_version": row["schema_version"],
            "payload": _canonical(row["payload"]),
        }
        for row in golden
    ]
    assert produced == canonical_golden

    for row, event in zip(golden, events, strict=True):
        assert decode(row["type"], row["schema_version"], row["payload"]) == event


def test_a_decoded_cell_is_a_cell_and_not_a_bare_tuple() -> None:
    """`Cell` is a NamedTuple, and both the walk and Pydantic have to keep it
    one. A plain tuple compares equal to a Cell, so nothing else in this file
    would notice the difference."""
    resolved = next(e for e in build_rich_stream().events if isinstance(e, DuelResolved))
    wire_type, version, payload = encode(resolved)
    decoded = decode(wire_type, version, payload)
    assert isinstance(decoded, DuelResolved)
    cell = next(iter(decoded.absorbed_cells))
    assert type(cell) is Cell
    assert (cell.col, cell.row) == (cell[0], cell[1])


def test_the_codec_refuses_a_container_it_does_not_understand() -> None:
    """A list or set reachable from an event would have its contents skipped
    by the datetime walk. No event field uses one today; if one ever does,
    this fails rather than losing a nested timestamp."""

    @dataclass(frozen=True)
    class WithAList:
        moments: list[datetime]

    with pytest.raises(TypeError):
        normalize_utc(WithAList([datetime(2026, 8, 22, 12, 0, tzinfo=UTC)]))
