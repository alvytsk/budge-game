"""The committed artifact, and the three levels of divergence check ruling 8
asks for.

The golden test compares generated text to committed text. The reachability
test in `test_schema.py` compares a list to a model tree. Both would pass if
the emitter were confidently wrong about what a frame actually contains —
which is what `test_the_frames_agree_with_what_the_server_actually_sends` is
for: it is the only test here that consults reality.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from domain.conftest import build_duel_state
from budge.api.projection import project_host, project_stage
from budge.contracts.export import check, render, write
from support.content import RecordingContentDirectory

NOW = datetime(2026, 8, 23, 18, 30, tzinfo=UTC)


def interface_body(emitted: str, name: str) -> str:
    start = emitted.index(f"export interface {name} {{")
    return emitted[start : emitted.index("}", start)]


# A property line, and only a property line: a long union is wrapped one
# member to a line, and `    | StartDuelCommand` must not read as a field
# called "| StartDuelCommand".
_PROPERTY = re.compile(r"^  (\w+)\??:")


def declared_properties(emitted: str, name: str) -> set[str]:
    return {
        match.group(1)
        for line in interface_body(emitted, name).splitlines()[1:]
        if (match := _PROPERTY.match(line)) is not None
    }


def test_the_committed_file_matches_the_models() -> None:
    """§11's «Контракты» row, as a test rather than only as a CI job — a
    developer who runs the suite finds out before pushing.

    Kills on: changing a model without regenerating, which is the exact
    divergence §7.6 exists to catch."""
    assert check() is None


def test_the_check_reports_a_diff_rather_than_a_boolean(tmp_path: Path) -> None:
    """Kills on: returning `False`. The reader of this failure has just
    changed a model, and «they differ» sends them to run the generator
    locally to discover how."""
    stale = tmp_path / "contracts.ts"
    stale.write_text("export interface StageFrame {}\n", encoding="utf-8")
    difference = check(stale)
    assert difference is not None
    assert "StageFrame" in difference


def test_the_check_treats_a_missing_file_as_a_difference(tmp_path: Path) -> None:
    """Kills on: raising `FileNotFoundError`, which turns a checkout with a
    deleted artifact into a crash instead of an instruction."""
    assert check(tmp_path / "nothing.ts") is not None


def test_writing_creates_the_directories(tmp_path: Path) -> None:
    """The frontend does not exist until plan 7, so the first run on a
    fresh checkout writes into directories nothing has created."""
    written = write(tmp_path / "a" / "b" / "contracts.ts")
    assert written.read_text(encoding="utf-8") == render()


def test_the_contract_contains_no_any() -> None:
    """Ruling 2, asserted on the output rather than on the emitter.

    Kills on: an escape hatch added to `_type_of` later — the emitter's own
    tests check the constructs it refuses today, and this checks that
    nothing in the *real* contract took a hatch added after them."""
    emitted = render()
    assert ": any" not in emitted
    assert ": unknown" not in emitted


def test_no_emitted_type_carries_a_url() -> None:
    """§7.6: «в сообщениях ездят идентификаторы, а не URL».

    Kills on: a frame gaining a `url`, `src` or `href` field in a later
    plan — plan 6's media is exactly where that temptation arrives."""
    emitted = render().lower()
    for forbidden in ("url:", "url?:", "src:", "src?:", "href:", "uri:"):
        assert forbidden not in emitted


def test_the_stage_frame_has_no_answer_field_in_typescript() -> None:
    """§7.1's leak, checked on the third rung of the ladder.

    The plan-4 tests walked the Python models and the JSON Schema. This
    walks the generated TypeScript — the thing the front end actually
    compiles against — so a leak introduced by the *emitter* is caught too.

    Kills on: an emitter that flattened `HostFrame`'s fields into
    `StageFrame`, or a `StageFrame` that gained a content field."""
    emitted = render()
    body = interface_body(emitted, "StageFrame")
    for forbidden in ("answer", "legal_attacks"):
        assert forbidden not in body
    duel = interface_body(emitted, "StageDuelFrame")
    assert "answer" not in duel


def test_the_host_frame_is_where_the_answer_is() -> None:
    """The control for the test above: a typo in either forbidden string
    would make it pass against a frame that leaked everything."""
    emitted = render()
    assert "current_answer" in interface_body(emitted, "HostDuelFrame")
    assert "legal_attacks" in interface_body(emitted, "HostFrame")


def test_a_hidden_category_has_only_its_discriminator() -> None:
    """Ruling 3 and plan 4's `HiddenCategory`, surviving codegen.

    Kills on: an emitter that dropped the `const` and left an empty
    interface — every object would then satisfy `HiddenCategory`, and the
    front end could not tell a secret from a named category at all."""
    assert declared_properties(render(), "HiddenCategory") == {"kind"}
    assert 'kind: "hidden";' in interface_body(render(), "HiddenCategory")


async def test_the_frames_agree_with_what_the_server_actually_sends() -> None:
    """Ruling 8's third level: a real frame, dumped, against the emitted
    interface's properties.

    Kills on: ruling 6 being wrong (a `const` field the server omits), or
    an emitter that dropped a property while translating it — neither of
    which any comparison between two generated artifacts could see."""
    state, *_ = build_duel_state()
    directory = RecordingContentDirectory()
    emitted = render()

    stage = await project_stage(state, now=NOW, events=(), directory=directory)
    host = await project_host(state, now=NOW, events=(), directory=directory)

    assert set(stage.model_dump(mode="json")) == declared_properties(emitted, "StageFrame")
    assert set(host.model_dump(mode="json")) == declared_properties(emitted, "HostFrame")


async def test_the_nested_frames_agree_too() -> None:
    """The top-level test above would pass against an emitter that got
    `TimingFrame` wrong, because `duel` is one property either way.

    Kills on: a nested model dropping or gaining a field in translation."""
    state, *_ = build_duel_state()
    emitted = render()

    stage = await project_stage(
        state, now=NOW, events=(), directory=RecordingContentDirectory()
    )
    assert stage.duel is not None

    dumped = stage.model_dump(mode="json")["duel"]
    assert set(dumped) == declared_properties(emitted, "StageDuelFrame")
    assert set(dumped["timing"]) == declared_properties(emitted, "TimingFrame")


def test_a_wrapped_union_does_not_read_as_a_property_name() -> None:
    """`declared_properties` underpins the reality checks above, and a
    wrapped union is indented like a property without being one.

    Kills on: parsing property names by splitting on ":" — every member of
    `Envelope.command` would become a field name, and the two agreement
    tests would compare a real frame against nonsense."""
    assert declared_properties(render(), "Envelope") == {"correlation_id", "command"}
