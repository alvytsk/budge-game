"""The emitter, construct by construct.

Hand-written documents rather than the real one: the real document
exercises whichever constructs today's models happen to use, and the
property under test is that each construct is translated correctly — and
that anything else is refused rather than guessed at.
"""

from typing import Any

import pytest

from podvinsya.contracts.typescript import UnsupportedSchema, emit


def document(**defs: Any) -> dict[str, Any]:
    return {"$defs": dict(defs)}


def obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def test_a_string_field_becomes_a_string() -> None:
    emitted = emit(document(A=obj({"name": {"type": "string"}}, ["name"])))
    assert "name: string;" in emitted


def test_an_integer_and_a_number_both_become_number() -> None:
    """TypeScript has one numeric type. Kills on: emitting `bigint` for an
    integer, which no JSON parser produces."""
    emitted = emit(
        document(A=obj({"i": {"type": "integer"}, "f": {"type": "number"}}, ["i", "f"]))
    )
    assert "i: number;" in emitted
    assert "f: number;" in emitted


def test_a_uuid_and_a_datetime_are_strings_on_the_wire() -> None:
    """Kills on: emitting `Date` for a date-time. `JSON.parse` produces a
    string, and a type saying otherwise makes every consumer wrong at the
    one place they would not think to check."""
    emitted = emit(
        document(
            A=obj(
                {
                    "id": {"type": "string", "format": "uuid"},
                    "at": {"type": "string", "format": "date-time"},
                },
                ["id", "at"],
            )
        )
    )
    assert "id: string;" in emitted
    assert "at: string;" in emitted


def test_an_optional_field_is_marked_optional() -> None:
    emitted = emit(document(A=obj({"name": {"type": "string"}}, [])))
    assert "name?: string;" in emitted


def test_a_nullable_field_is_a_union_with_null() -> None:
    emitted = emit(
        document(A=obj({"n": {"anyOf": [{"type": "string"}, {"type": "null"}]}}, ["n"]))
    )
    assert "n: string | null;" in emitted


def test_a_const_property_is_required_even_when_the_schema_says_otherwise() -> None:
    """Ruling 6. Kills on: honouring `required` for a discriminator —
    TypeScript cannot narrow a union on a field that might be absent, so
    every `kind` and every `type` in this contract would stop working as a
    discriminant and consumers would need a cast."""
    emitted = emit(
        document(
            A=obj({"kind": {"const": "ack", "type": "string"}, "x": {"type": "string"}}, [])
        )
    )
    assert 'kind: "ack";' in emitted
    assert "x?: string;" in emitted


def test_an_enum_becomes_a_union_of_literals() -> None:
    emitted = emit(document(S={"type": "string", "enum": ["setup", "running"]}))
    assert 'export type S = "setup" | "running";' in emitted


def test_an_array_becomes_an_array_type() -> None:
    emitted = emit(
        document(A=obj({"cells": {"type": "array", "items": {"$ref": "#/$defs/C"}}}, ["cells"]))
    )
    assert "cells: C[];" in emitted


def test_an_array_of_a_union_is_parenthesised() -> None:
    """Kills on: emitting `A | B[]`, which TypeScript reads as
    `A | (B[])` — an array of B, or a bare A. Every consumer that mapped
    over it would be wrong for the first branch."""
    emitted = emit(
        document(
            A=obj(
                {
                    "xs": {
                        "type": "array",
                        "items": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    }
                },
                ["xs"],
            )
        )
    )
    assert "xs: (string | null)[];" in emitted


def test_a_mapping_becomes_a_record() -> None:
    """`dict[UUID, int]` on a frame. Kills on: emitting `object`, which
    loses the value type and makes `remaining_ms[playerId]` an `any`."""
    emitted = emit(
        document(
            A=obj(
                {
                    "m": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                        "propertyNames": {"format": "uuid"},
                    }
                },
                ["m"],
            )
        )
    )
    assert "m: Record<string, number>;" in emitted


def test_a_reference_becomes_the_referenced_name() -> None:
    emitted = emit(document(A=obj({"b": {"$ref": "#/$defs/B"}}, ["b"]), B=obj({}, [])))
    assert "b: B;" in emitted


def test_a_discriminated_union_becomes_a_union() -> None:
    """Kills on: emitting only the first branch, which would make every
    command but `declare_attack` a type error at the call site."""
    emitted = emit(
        document(
            A=obj(
                {
                    "c": {
                        "oneOf": [{"$ref": "#/$defs/X"}, {"$ref": "#/$defs/Y"}],
                        "discriminator": {"propertyName": "type", "mapping": {}},
                    }
                },
                ["c"],
            ),
            X=obj({}, []),
            Y=obj({}, []),
        )
    )
    assert "c: X | Y;" in emitted


def test_definitions_are_emitted_in_a_stable_order() -> None:
    """The artifact is committed and diffed, so a run-to-run reordering
    would fail CI for no reason.

    Kills on: iterating a set, or emitting in `$defs` insertion order —
    which follows model *discovery* order and changes when an import moves."""
    first = emit(document(B=obj({}, []), A=obj({}, [])))
    second = emit(document(A=obj({}, []), B=obj({}, [])))
    assert first == second
    assert first.index("interface A") < first.index("interface B")


def test_the_header_says_it_is_generated_and_how_to_regenerate_it() -> None:
    """Kills on: dropping the header. The first thing anyone does with an
    unfamiliar checked-in file is edit it."""
    emitted = emit(document(A=obj({}, [])))
    assert "podvinsya export-types" in emitted
    assert "do not edit" in emitted.lower()


def test_a_quote_in_a_literal_is_escaped() -> None:
    """Kills on: interpolating the value straight into the source, which
    produces TypeScript that does not parse — and the CI check compares
    text, so nothing else would notice."""
    emitted = emit(document(S={"type": "string", "enum": ['say "hi"']}))
    assert r'"say \"hi\""' in emitted


@pytest.mark.parametrize(
    "node",
    [
        {"type": "array"},
        {"type": "array", "items": {"type": "string"}, "prefixItems": [{"type": "string"}]},
        {"allOf": [{"$ref": "#/$defs/B"}]},
        {"not": {"type": "string"}},
        {"type": "unheard-of"},
        {},
        {"type": "object"},
    ],
    ids=["untyped-array", "tuple", "allOf", "not", "unknown-type", "empty", "open-object"],
)
def test_a_construct_the_emitter_does_not_understand_is_refused(node: dict[str, Any]) -> None:
    """Ruling 2, and the reason this generator can be trusted.

    Kills on: falling back to `any` or `unknown`. The TypeScript would
    still compile, the CI check would still pass, and the contract would
    silently stop being one — which is the exact failure §7.6 is written
    to prevent."""
    with pytest.raises(UnsupportedSchema):
        emit(document(A=obj({"f": node}, ["f"])))


def test_the_refusal_names_where_it_happened() -> None:
    """Kills on: raising a bare message. The document is 36 definitions
    deep; "unsupported schema" without a path is a message that sends a
    reader to read all of them."""
    with pytest.raises(UnsupportedSchema, match=r"A\.f"):
        emit(document(A=obj({"f": {"not": {"type": "string"}}}, ["f"])))


def test_an_unknown_keyword_beside_a_known_type_is_refused() -> None:
    """A `string` with a `pattern` is not a `string`: the constraint is
    part of the contract, and dropping it silently would tell the front end
    that any string will do.

    Kills on: matching on `type` alone and ignoring every sibling key."""
    with pytest.raises(UnsupportedSchema):
        emit(document(A=obj({"f": {"type": "string", "pattern": "^a"}}, ["f"])))


def test_a_long_union_is_wrapped_one_member_to_a_line() -> None:
    """The artifact is committed and its CI failure is read as a diff.

    Kills on: emitting a union on one line — adding one command to the
    seven-member union would rewrite a 158-character line, and the reviewer
    would have to diff it by eye to see which member was new."""
    long_union = {"anyOf": [{"$ref": f"#/$defs/AVeryLongCommandName{i}"} for i in range(7)]}
    emitted = emit(document(A=obj({"command": long_union}, ["command"])))
    assert "  command:\n    | AVeryLongCommandName0\n" in emitted
    assert all(len(line) <= 100 for line in emitted.splitlines())


def test_a_short_union_stays_on_one_line() -> None:
    """Kills on: wrapping everything, which would make every nullable
    field in the contract three lines long."""
    emitted = emit(
        document(A=obj({"n": {"anyOf": [{"type": "string"}, {"type": "null"}]}}, ["n"]))
    )
    assert "  n: string | null;" in emitted


def test_a_union_nested_inside_brackets_is_not_split() -> None:
    """Kills on: splitting on every ` | `, which would cut
    `Record<string, A | B>` in half and emit two members that are not
    types — TypeScript that does not parse, in a file compared as text."""
    inner = {"anyOf": [{"$ref": f"#/$defs/LongEnoughToForceWrapping{i}"} for i in range(4)]}
    emitted = emit(
        document(
            A=obj({"m": {"type": "object", "additionalProperties": inner}}, ["m"])
        )
    )
    assert "Record<string, LongEnoughToForceWrapping0 | LongEnoughToForceWrapping1" in emitted
