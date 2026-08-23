"""What the two frames can hold, asserted against the schema rather than
against a value.

The §11 whole-tree test in `test_projection.py` walks a *populated* frame
and proves no answer is in it. These tests are its pair: they walk the
*schema* and prove no field could hold one. A field added but never
populated passes the first and fails these.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from podvinsya.api.schemas.frames import (
    HiddenCategory,
    HostCategory,
    HostFrame,
    NamedCategory,
    StageFrame,
    TimingFrame,
)
from support.walk import property_names

# Anything whose name says "the correct answer". The stage never learns one.
FORBIDDEN_PROPERTIES = frozenset(
    {"answer", "answers", "current_answer", "image_answers", "correct_answer"}
)


def test_a_stage_frame_has_no_field_anywhere_that_could_hold_an_answer() -> None:
    """Kills on: adding a field named for content to any model reachable
    from `StageFrame` — the shape the §11 whole-tree test cannot catch
    until something actually sets a value in it."""
    assert not (property_names(StageFrame.model_json_schema()) & FORBIDDEN_PROPERTIES)


def test_the_host_frame_is_where_the_answer_lives() -> None:
    """The negative test above proves nothing on its own: a typo in
    `FORBIDDEN_PROPERTIES` would make it pass against every frame. This
    asserts the same walk finds the answer where it is supposed to be."""
    assert property_names(HostFrame.model_json_schema()) & FORBIDDEN_PROPERTIES


def test_a_hidden_category_carries_no_fields() -> None:
    """Kills on: giving `HiddenCategory` a `name: str | None`, which would
    turn «Секрет» from a variant the server cannot fill into a value it
    could fill by mistake."""
    assert set(HiddenCategory.model_fields) == {"kind"}


def test_the_stage_category_union_has_exactly_two_variants() -> None:
    """Kills on: a third variant appearing without the leak analysis in
    ruling 3 being redone."""
    schema = StageFrame.model_json_schema()
    variants = schema["$defs"]["StageGroupFrame"]["properties"]["category"]["anyOf"]
    assert len(variants) == 2
    assert {NamedCategory.__name__, HiddenCategory.__name__} == {
        variant["$ref"].rsplit("/", 1)[-1] for variant in variants
    }


def test_a_host_category_may_be_nameless() -> None:
    """Ruling 3: a category the directory cannot name is `null` to the host,
    where an operator can see it, rather than silently omitted."""
    nameless = HostCategory(id=uuid4(), name=None)
    assert nameless.name is None


def test_timing_carries_both_players() -> None:
    """Kills on: projecting only the answering player's remainder, which
    §7.3 forbids — the idle player's timer would become a client guess."""
    assert "remaining_ms" in TimingFrame.model_fields
    assert TimingFrame.model_fields["remaining_ms"].annotation is not None


def test_a_frame_cannot_be_edited_after_it_is_built() -> None:
    """Kills on: dropping `frozen=True`. A frame is what was true at one
    `seq`; a mutable one invites a caller to patch it and pass it on as
    something the server said."""
    category = NamedCategory(name="История")
    with pytest.raises(ValidationError):
        category.name = "Кино"  # type: ignore[misc]
