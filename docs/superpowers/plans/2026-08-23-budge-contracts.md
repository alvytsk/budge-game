# Контракты «Подвинься» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make §7.6's «схемы генерируются из Pydantic в TypeScript, расхождение ловится в CI» true — one command that turns the plan-4 models into a TypeScript file, that file committed, and a CI job that fails the moment the two disagree.

**Architecture:** Three small modules and one artifact. `contracts/schema.py` names the root models explicitly and asks Pydantic for one combined JSON Schema document over all of them. `contracts/typescript.py` turns that document into TypeScript — and *refuses* any construct it does not understand rather than emitting `any`, because a generator that guesses is a generator whose output nobody can trust. `contracts/export.py` writes the file, or compares it and reports the difference. The check runs in CI, which this plan also creates: there is none today, so «валит CI» has nothing to fail until this plan adds it.

**Tech Stack:** Python 3.12, Pydantic v2's `models_json_schema`, the standard library. No npm generator and no node in the backend's CI (see ruling 1).

**Spec:** `docs/superpowers/specs/2026-08-22-budge-design.md` — §7.6 is this plan's mandate, §11's «Контракты» row is what its CI job owes, and §9.3 says where the generated file has to land: «Обе поверхности живут в одном приложении и делят сгенерированные типы».

**Branch:** `feature/contracts`, cut from `feature/api`, which is cut from `feature/runtime` → `feature/persistence` → `main`, none of them merged. Nothing under `domain/`, `db/`, `runtime/` or `api/` is modified by this plan; `cli.py` gains one subcommand.

## Global Constraints

- Python `>=3.12`. `mypy --strict` clean over `src/budge` and `tests`; `ruff check` clean with `select = ["E4", "E7", "E9", "F", "E501"]` and `line-length = 100`.
- Dependency direction stays one-way. `contracts/` imports `api/schemas/` and nothing else from the application; nothing imports `contracts/`.
- «Схемы генерируются из Pydantic в TypeScript, расхождение ловится в CI.» (§7.6) The generated file is committed, and the check is a comparison against it — a file generated at build time and never committed has nothing to diverge *from*.
- «Медиа контент-адресуемо по sha256; в сообщениях ездят идентификаторы, а не URL.» (§7.6) Unchanged by this plan and asserted by it: no emitted type carries a URL.
- «Обе поверхности живут в одном приложении и делят сгенерированные типы.» (§9.3) One file, both surfaces, at the FSD `shared/` layer.
- The stage screen's leak-proofness (§7.1) survives codegen: the emitted `StageFrame` has no field that could hold an answer, and a test walks the *TypeScript* to say so — the plan-4 tests walked the Python and the JSON Schema, and this is the third rung of the same ladder.
- Code, identifiers and comments in English. The generated file carries a header saying it is generated and how to regenerate it.
- **Every test states what would kill it.** Every task report names, for each test, the single change to the code under test that would make it fail.

## Rulings made while writing this plan

1. **The emitter is Python, not an npm generator.** `json-schema-to-typescript` is mature, but it would put node in the backend's CI for a frontend that does not exist until plan 7, and its output changes between versions — which makes a committed-artifact diff fail for reasons that have nothing to do with the contract. The model set here is closed and small (36 definitions today), and the constructs Pydantic emits for it are enumerable. *Cost if wrong: swapping in a generator later replaces one module and regenerates one file.*
2. **The emitter refuses what it does not understand.** An unrecognised schema node raises `UnsupportedSchema` naming the path to it, rather than emitting `any` or `unknown`. A generator that degrades quietly produces TypeScript that compiles and lies, and the CI check would go on passing. *Cost if wrong: adding an exotic field type to a frame requires one commit to the emitter — which is the intent, not the cost.*
3. **Roots are listed explicitly; a test proves the list is complete.** Auto-discovering every `BaseModel` under `api/` would sweep in internal types and make the artifact churn whenever an implementation detail changed. So the roots are a literal — and `test_every_schema_model_is_reachable_from_a_root` walks `api/schemas/` and fails if a model there is not reachable from one, which is the failure mode a literal has. *Cost if wrong: a new model needs one line in the root list, and the test says so by name.*
4. **The document is built from the models, not from FastAPI's OpenAPI.** §7.6's contract is the schemas. The OpenAPI document additionally carries paths, status codes and FastAPI's own `HTTPValidationError`, all of which change shape with the framework version — so the artifact would churn on a FastAPI upgrade that changed no contract at all. *Cost if wrong: a route added with no model change is not caught; routes are not what §7.6 calls the contract.*
5. **Outbound models are exported in serialization mode, inbound in validation mode.** `models_json_schema` takes a mode per model, and the two questions genuinely differ: a frame's schema is «what will the server send», a body's is «what may a client send». *Cost if wrong: for today's models the two modes agree, so the cost is zero until a computed field or a serialization alias appears — at which point mixing them would have been the bug.*
6. **A property whose schema is a `const` is emitted as required, whatever `required` says.** Pydantic leaves a field with a default out of `required`, and every discriminator in this contract (`kind`, `type`) has one. Emitted as optional, they would be useless for narrowing — TypeScript cannot discriminate a union on a field that might be absent. The server always serialises them. *Cost if wrong: a future model with a defaulted `const` that really is omitted would be mistyped; no such model exists, and ruling 8's value-level test would catch one.*
7. **The generated file lives at `frontend/src/shared/api/contracts.ts`.** §9.3 fixes both the stack (Vite + React + FSD) and the sharing (one application, two surfaces, shared generated types), and FSD puts cross-surface types at `shared/`. Creating the two directories now, holding one generated file, is what lets plan 7 start by consuming a contract instead of by inventing one. *Cost if wrong: plan 7 moves one file and one constant.*
8. **The divergence check is asserted at three levels, not one.** The golden test compares the emitted text to the committed file; the reachability test compares the root list to the model tree; and a value-level test dumps a *real* `StageFrame` and `HostFrame` and asserts their JSON keys are exactly the emitted interface's required properties. The first two compare generated things to generated things and would both pass if the emitter were confidently wrong about what the server actually sends. *Cost if wrong: one more test to update when a frame gains a field — which is a field the contract must be told about anyway.*
9. **CI is GitHub Actions, and this plan creates it.** `origin` is `git@github.com:alvytsk/budge-game.git`, and there is no workflow in the repository. §11 makes «расхождение Pydantic и TypeScript валит CI» a requirement, and it cannot be met by a check nothing runs. The workflow also runs the existing suite, ruff and mypy — a CI that ran only the contract check would be a strange first workflow. *Cost if wrong: a move to another forge rewrites one YAML file.*

10. **A constraint TypeScript cannot express is documented, not dropped.** Added while executing the library plan, whose `media_sha256` is the first field in the contract with a `pattern`. Emitting a bare `string` would tell a front end that any string will do — the silent degradation ruling 2 forbids, arriving through a keyword instead of through a fallback. So an enumerated set of constraints (`pattern`, `minLength`, `maximum`, …) is carried into the output as a doc comment above the property, and every keyword outside that set still raises. *Cost if wrong: a reader has to trust a comment rather than a type — which is all TypeScript can offer for a regex, and strictly more than nothing.*

## File Structure

```
backend/src/budge/contracts/__init__.py      create  the public surface
backend/src/budge/contracts/schema.py        create  ROOTS, contract_schema()
backend/src/budge/contracts/typescript.py    create  emit(), UnsupportedSchema
backend/src/budge/contracts/export.py        create  write(), check(), CONTRACTS_PATH
backend/src/budge/cli.py                     modify  export-types
backend/tests/contracts/__init__.py              create
backend/tests/contracts/test_schema.py           create  roots and reachability
backend/tests/contracts/test_typescript.py       create  the emitter, construct by construct
backend/tests/contracts/test_export.py           create  golden file, --check, leak walk
backend/tests/test_cli.py                        modify  export-types
frontend/src/shared/api/contracts.ts             create  generated, committed
.github/workflows/ci.yml                         create  backend suite + contract check
```

---

### Task 1: One schema document, and proof that nothing was left out of it

The whole contract as a single JSON Schema document with one shared `$defs`,
built from an explicit list of roots — plus the test that makes the explicit
list safe.

**Files:**
- Create: `backend/src/budge/contracts/__init__.py`
- Create: `backend/src/budge/contracts/schema.py`
- Create: `backend/tests/contracts/__init__.py`
- Test: `backend/tests/contracts/test_schema.py`

**Interfaces:**
- Produces: `ROOTS: tuple[tuple[type[BaseModel], JsonSchemaMode], ...]`,
  `contract_schema() -> dict[str, Any]` (a document whose only key is `$defs`).
- Consumes: `budge.api.schemas.frames`, `.commands`, `.rest`.

- [ ] **Step 1: Write `contracts/schema.py`**

```python
"""The contract, as one JSON Schema document.

§7.6 makes the *schemas* the contract, so this is built from the models
rather than from FastAPI's OpenAPI document (ruling 4): the OpenAPI adds
paths, status codes and the framework's own error model, all of which
change shape on a FastAPI upgrade that changed no contract at all.

`ROOTS` is a literal, not a scan (ruling 3). The failure mode of a literal
is forgetting to add to it, and that failure has a test with a name:
`test_every_schema_model_is_reachable_from_a_root`.
"""

from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode, models_json_schema

from budge.api.schemas.commands import Ack, Envelope
from budge.api.schemas.frames import HostFrame, StageFrame
from budge.api.schemas.rest import (
    AddPlayerBody,
    AssignSecretBody,
    CreateMatchBody,
    CreatedMatchBody,
    LoginBody,
    MatchSummaryBody,
    OutcomeBody,
    SnapshotBody,
)

# Ruling 5: a frame's schema answers "what will the server send", a body's
# answers "what may a client send". `models_json_schema` takes the mode per
# model, so the two questions are asked separately rather than collapsed.
ROOTS: tuple[tuple[type[BaseModel], JsonSchemaMode], ...] = (
    (StageFrame, "serialization"),
    (HostFrame, "serialization"),
    (Ack, "serialization"),
    (CreatedMatchBody, "serialization"),
    (MatchSummaryBody, "serialization"),
    (SnapshotBody, "serialization"),
    (OutcomeBody, "serialization"),
    (Envelope, "validation"),
    (LoginBody, "validation"),
    (CreateMatchBody, "validation"),
    (AddPlayerBody, "validation"),
    (AssignSecretBody, "validation"),
)

REF_TEMPLATE = "#/$defs/{model}"


def contract_schema() -> dict[str, Any]:
    """One document, one `$defs`, every root resolved into it.

    `models_json_schema` is what makes this one document rather than twelve:
    asking each model separately would produce twelve copies of `CellFrame`,
    and the emitted TypeScript would carry twelve names for one type.
    """
    _mapping, definitions = models_json_schema(list(ROOTS), ref_template=REF_TEMPLATE)
    return dict(definitions)
```

- [ ] **Step 2: Write `contracts/__init__.py`**

```python
"""Turning the plan-4 Pydantic models into TypeScript, and keeping the two
from drifting apart (§7.6)."""
```

- [ ] **Step 3: Write the failing tests**

```python
"""The contract document, and the one thing an explicit root list can get
wrong."""

import inspect

from pydantic import BaseModel

from budge.api.schemas import commands, frames, rest
from budge.contracts.schema import ROOTS, contract_schema

SCHEMA_MODULES = (frames, commands, rest)

# Base classes, not contract members: they carry configuration and no
# fields, and emitting them would put two empty interfaces in the artifact
# that no wire message is ever an instance of.
BASE_MODELS = {"Frozen", "Inbound", "Body"}


def models_in(module: object) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(module, inspect.isclass)
        if issubclass(member, BaseModel)
        and member.__module__ == module.__name__
        and name not in BASE_MODELS
    }


def test_the_document_has_one_shared_defs() -> None:
    """Kills on: asking each root for its own schema and merging the
    results, which gives one `$defs` entry per (model, root) pair — twelve
    `CellFrame`s in the emitted TypeScript, all identical, none equal."""
    document = contract_schema()
    assert set(document) == {"$defs"}
    assert document["$defs"]["CellFrame"]["type"] == "object"


def test_every_schema_model_is_reachable_from_a_root() -> None:
    """The failure mode of ruling 3's explicit list, given a name.

    Kills on: adding a model to `api/schemas/` and forgetting to export it —
    the front end would then hand-write a type for a message the server
    sends, which is exactly the divergence §7.6 exists to prevent."""
    exported = set(contract_schema()["$defs"])
    declared = set().union(*(models_in(module) for module in SCHEMA_MODULES))
    assert not (declared - exported)


def test_the_base_model_exemptions_still_exist() -> None:
    """A name left in `BASE_MODELS` after the class was deleted would
    silently exempt nothing, and a typo would silently exempt a real model.

    Kills on: either — the exemption list must name classes that exist and
    that genuinely have no fields."""
    for module in SCHEMA_MODULES:
        for name, member in inspect.getmembers(module, inspect.isclass):
            if name in BASE_MODELS and member.__module__ == module.__name__:
                assert not member.model_fields


def test_every_root_is_a_model_in_a_schema_module() -> None:
    """Kills on: exporting a model that lives outside `api/schemas/` — a
    domain dataclass or a service type — which would put a shape on the
    wire contract that no route or socket ever sends."""
    declared = set().union(*(models_in(module) for module in SCHEMA_MODULES))
    assert {model.__name__ for model, _mode in ROOTS} <= declared
```

- [ ] **Step 4: Run them**

`pytest tests/contracts/test_schema.py -q` from `backend/`. Expected: pass.
Then `mypy` and `ruff check`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/budge/contracts backend/tests/contracts
git commit -m "Gather every wire model into one schema document"
```

---

### Task 2: The emitter, which refuses what it cannot express

The whole of ruling 2. Every construct Pydantic produces for these models
is handled by name; anything else raises, carrying the path to the node
that was not understood.

**Files:**
- Create: `backend/src/budge/contracts/typescript.py`
- Test: `backend/tests/contracts/test_typescript.py`

**Interfaces:**
- Produces: `UnsupportedSchema(Exception)`, `emit(document: dict[str, Any]) -> str`.
- Consumes: nothing but the document Task 1 produces.

- [ ] **Step 1: Write the failing tests**

Each test drives `emit` with a hand-written document, so the emitter is
tested against constructs rather than against whatever the models happen to
contain today.

```python
"""The emitter, construct by construct.

Hand-written documents rather than the real one: the real document
exercises whichever constructs today's models happen to use, and the
property under test is that each construct is translated correctly — and
that anything else is refused rather than guessed at.
"""

from typing import Any

import pytest

from budge.contracts.typescript import UnsupportedSchema, emit


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
        document(
            A=obj({"n": {"anyOf": [{"type": "string"}, {"type": "null"}]}}, ["n"])
        )
    )
    assert "n: string | null;" in emitted


def test_a_const_property_is_required_even_when_the_schema_says_otherwise() -> None:
    """Ruling 6. Kills on: honouring `required` for a discriminator —
    TypeScript cannot narrow a union on a field that might be absent, so
    every `kind` and every `type` in this contract would stop working as a
    discriminant and consumers would need a cast."""
    emitted = emit(
        document(A=obj({"kind": {"const": "ack", "type": "string"}, "x": {"type": "string"}}, []))
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
    assert "budge export-types" in emitted.splitlines()[1]
    assert "do not edit" in emitted.lower()


@pytest.mark.parametrize(
    "node",
    [
        {"type": "array"},
        {"type": "array", "prefixItems": [{"type": "string"}]},
        {"allOf": [{"$ref": "#/$defs/B"}]},
        {"not": {"type": "string"}},
        {"type": "unheard-of"},
        {},
    ],
    ids=["untyped-array", "tuple", "allOf", "not", "unknown-type", "empty"],
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
```

- [ ] **Step 2: Run them and watch them fail**

`pytest tests/contracts/test_typescript.py -q`. Expected: collection error —
`budge.contracts.typescript` does not exist.

- [ ] **Step 3: Write `contracts/typescript.py`**

```python
"""JSON Schema to TypeScript, for exactly the constructs this contract uses.

Ruling 2 is the whole design: every node is matched against a construct
this emitter knows, and anything left over raises `UnsupportedSchema`
naming the path to it. There is no `any` fallback and no `unknown`
fallback. A generator that degrades quietly emits TypeScript that compiles
and lies, and the CI check in §11 would go on passing over it — which
would make the check worse than useless, because it would be believed.

Everything is emitted alphabetically. The artifact is committed and
compared, so a stable order is a correctness property: `$defs` arrives in
model *discovery* order, which changes when an import moves.
"""

from typing import Any

HEADER = """// Generated from the Pydantic models by `budge export-types`.
// Do not edit: run `budge export-types` and commit the result.
// The CI job `contracts` fails if this file and the models disagree (§7.6).
"""

_PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}

# Annotation, not structure: none of these changes what a value may be, so
# an emitter that ignores them is not guessing.
_IGNORED_KEYS = frozenset(
    {"title", "description", "default", "examples", "propertyNames", "additionalProperties"}
)


class UnsupportedSchema(Exception):
    """A schema node this emitter will not guess at.

    Raised rather than degraded to `any`, so a construct nobody has taught
    this module about stops the export instead of producing a contract that
    is quietly wrong.
    """

    def __init__(self, path: str, node: object) -> None:
        super().__init__(f"{path}: this emitter does not understand {node!r}")
        self.path = path


def _literal(value: object) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise UnsupportedSchema("<literal>", value)


def _type_of(node: dict[str, Any], path: str) -> str:
    """One schema node to one TypeScript type expression."""
    if "$ref" in node:
        return str(node["$ref"]).rsplit("/", 1)[-1]
    if "const" in node:
        return _literal(node["const"])
    if "enum" in node:
        return " | ".join(_literal(value) for value in node["enum"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            return " | ".join(
                _type_of(branch, f"{path}[{index}]")
                for index, branch in enumerate(node[key])
            )

    kind = node.get("type")
    if kind == "array":
        items = node.get("items")
        if not isinstance(items, dict):
            # An array with no `items` is a list of anything, which is the
            # one shape this contract must never contain: it would be the
            # `any` this emitter refuses, arriving through the back door.
            raise UnsupportedSchema(path, node)
        if "prefixItems" in node:
            raise UnsupportedSchema(path, node)
        inner = _type_of(items, f"{path}[]")
        return f"({inner})[]" if " " in inner else f"{inner}[]"
    if kind == "object":
        values = node.get("additionalProperties")
        if isinstance(values, dict):
            return f"Record<string, {_type_of(values, f'{path}{{}}')}>"
        raise UnsupportedSchema(path, node)
    if isinstance(kind, str) and kind in _PRIMITIVES:
        unknown = set(node) - _IGNORED_KEYS - {"type", "format"}
        if unknown:
            raise UnsupportedSchema(path, node)
        return _PRIMITIVES[kind]
    raise UnsupportedSchema(path, node)


def _interface(name: str, node: dict[str, Any]) -> str:
    required = set(node.get("required", ()))
    lines = [f"export interface {name} {{"]
    for field, schema in node.get("properties", {}).items():
        # Ruling 6: a `const` is always sent and is what a consumer narrows
        # on, so it is required whatever `required` says.
        optional = field not in required and "const" not in schema
        mark = "?" if optional else ""
        lines.append(f"  {field}{mark}: {_type_of(schema, f'{name}.{field}')};")
    lines.append("}")
    return "\n".join(lines)


def _alias(name: str, node: dict[str, Any]) -> str:
    return f"export type {name} = {_type_of(node, name)};"


def emit(document: dict[str, Any]) -> str:
    """The whole document as one TypeScript module."""
    definitions = document["$defs"]
    blocks = []
    for name in sorted(definitions):
        node = definitions[name]
        if node.get("type") == "object" and "properties" in node:
            blocks.append(_interface(name, node))
        else:
            blocks.append(_alias(name, node))
    return HEADER + "\n" + "\n\n".join(blocks) + "\n"
```

- [ ] **Step 4: Run the tests**

`pytest tests/contracts/test_typescript.py -q`. Expected: all pass. Then
`mypy` and `ruff check`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/budge/contracts/typescript.py backend/tests/contracts/test_typescript.py
git commit -m "Emit TypeScript for what this contract uses, and refuse the rest"
```

---

### Task 3: The artifact, the command, and the check that fails CI

**Files:**
- Create: `backend/src/budge/contracts/export.py`
- Modify: `backend/src/budge/cli.py`
- Create: `frontend/src/shared/api/contracts.ts` (generated)
- Test: `backend/tests/contracts/test_export.py`
- Test: `backend/tests/test_cli.py` (extend)

**Interfaces:**
- Produces: `CONTRACTS_PATH: Path`, `render() -> str`, `write() -> Path`,
  `check() -> str | None` (the unified diff, or `None` when they agree).

- [ ] **Step 1: Write `contracts/export.py`**

```python
"""Where the generated TypeScript goes, and how divergence is reported.

§7.6 wants divergence caught in CI, which means the artifact is committed
and the check is a comparison against it. A file generated at build time
and never committed has nothing to diverge *from* — the check would
regenerate it and compare it with itself.
"""

import difflib
from pathlib import Path

from budge.contracts.schema import contract_schema
from budge.contracts.typescript import emit

# `backend/src/budge/contracts/export.py` → the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[4]

# §9.3: «Обе поверхности живут в одном приложении и делят сгенерированные
# типы», and FSD puts what both surfaces share at the `shared/` layer.
CONTRACTS_PATH = _REPO_ROOT / "frontend" / "src" / "shared" / "api" / "contracts.ts"


def render() -> str:
    return emit(contract_schema())


def write(path: Path | None = None) -> Path:
    target = path or CONTRACTS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    return target


def check(path: Path | None = None) -> str | None:
    """The unified diff between the models and the committed file, or
    `None` when they agree.

    A diff rather than a boolean: the CI failure this produces is read by
    somebody who has just changed a model, and «they differ» would send
    them to run the generator locally to find out how.
    """
    target = path or CONTRACTS_PATH
    expected = render()
    actual = target.read_text(encoding="utf-8") if target.exists() else ""
    if actual == expected:
        return None
    return "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{target} (committed)",
            tofile="generated from the Pydantic models",
        )
    )
```

- [ ] **Step 2: Add `budge export-types`**

In `cli.py`, alongside the other subcommands:

```python
    export_types = subcommands.add_parser(
        "export-types", help="generate the TypeScript contract from the Pydantic models"
    )
    export_types.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit non-zero and print a diff if the file is out of date",
    )
```

and in `main`:

```python
    if args.command == "export-types":
        # Imported here for the same reason `serve`'s imports are: the
        # migrate step must not pull in the whole API model tree.
        from budge.contracts.export import CONTRACTS_PATH, check, write

        if args.check:
            difference = check()
            if difference is None:
                return 0
            print(difference, end="")
            print(
                f"\n{CONTRACTS_PATH} is out of date. "
                "Run `budge export-types` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(write())
        return 0
```

- [ ] **Step 3: Generate and commit the artifact**

```bash
.venv/bin/budge export-types
```

Then read the produced file. It must contain `export interface StageFrame`,
`export interface HostFrame`, and no occurrence of `any`.

- [ ] **Step 4: Write the tests**

```python
"""The committed artifact, and the three levels ruling 8 asks for."""

from pathlib import Path

import pytest

from budge.contracts.export import CONTRACTS_PATH, check, render, write


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
    """Kills on: raising `FileNotFoundError`, which turns a first checkout
    with a deleted artifact into a crash instead of an instruction."""
    assert check(tmp_path / "nothing.ts") is not None


def test_writing_creates_the_directories(tmp_path: Path) -> None:
    written = write(tmp_path / "a" / "b" / "contracts.ts")
    assert written.read_text(encoding="utf-8") == render()


def test_the_contract_contains_no_any() -> None:
    """Ruling 2, asserted on the output rather than on the emitter.

    Kills on: any escape hatch added to `_type_of` — the emitter's own
    tests check the constructs it refuses, and this checks that nothing in
    the *real* contract took an escape hatch that was added later."""
    emitted = render()
    assert ": any" not in emitted
    assert ": unknown" not in emitted


def test_no_emitted_type_carries_a_url() -> None:
    """§7.6: «в сообщениях ездят идентификаторы, а не URL».

    Kills on: a frame gaining a `url` or `src` field in a later plan —
    plan 6's media is exactly where that temptation arrives."""
    emitted = render().lower()
    for forbidden in ("url:", "url?:", "src:", "href:", "uri:"):
        assert forbidden not in emitted


def test_the_stage_frame_has_no_answer_field_in_typescript() -> None:
    """§7.1's leak, checked on the third rung of the ladder.

    The plan-4 tests walked the Python models and the JSON Schema. This
    walks the generated TypeScript — the thing the front end actually
    compiles against — so a leak introduced by the *emitter* is caught too.

    Kills on: an emitter that flattened `HostFrame`'s fields into
    `StageFrame`, or a `StageFrame` that gained a content field."""
    emitted = render()
    start = emitted.index("export interface StageFrame {")
    body = emitted[start : emitted.index("}", start)]
    for forbidden in ("answer", "current_answer", "legal_attacks"):
        assert forbidden not in body


def test_the_frames_agree_with_what_the_server_actually_sends() -> None:
    """Ruling 8's third level: a real frame, dumped, against the emitted
    interface's required properties.

    The golden test compares generated text to generated text, and the
    reachability test compares a list to a tree — both would pass if the
    emitter were confidently wrong about what a frame contains. This is the
    only test here that consults reality.

    Kills on: ruling 6 being wrong (a `const` field the server omits), or
    an emitter that dropped a property while translating it."""
    import asyncio

    from domain.conftest import build_duel_state
    from budge.api.projection import project_stage
    from support.content import RecordingContentDirectory
    from datetime import UTC, datetime

    state, *_ = build_duel_state()
    frame = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        project_stage(
            state,
            now=datetime(2026, 8, 23, tzinfo=UTC),
            events=(),
            directory=RecordingContentDirectory(),
        )
    )
    sent = set(frame.model_dump(mode="json"))

    emitted = render()
    start = emitted.index("export interface StageFrame {")
    body = emitted[start : emitted.index("}", start)]
    declared = {
        line.strip().split(":")[0].rstrip("?")
        for line in body.splitlines()[1:]
        if line.strip()
    }
    assert sent == declared
```

- [ ] **Step 5: Extend `tests/test_cli.py`**

```python
def test_export_types_check_passes_against_the_committed_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Kills on: `--check` writing the file, which would make the CI job
    pass by fixing the divergence instead of reporting it."""
    assert main(["export-types", "--check"]) == 0


def test_export_types_check_fails_against_a_stale_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§11: «расхождение Pydantic и TypeScript валит CI» — a non-zero exit
    is what "fails CI" means mechanically.

    Kills on: returning 0 on a difference, which leaves the CI job green
    over a contract that has drifted."""
    from budge.contracts import export

    stale = tmp_path / "contracts.ts"
    stale.write_text("nothing like the real thing\n", encoding="utf-8")
    monkeypatch.setattr(export, "CONTRACTS_PATH", stale)

    assert main(["export-types", "--check"]) == 1
    assert "out of date" in capsys.readouterr().err
```

- [ ] **Step 6: Run everything**

`pytest -q`, `mypy`, `ruff check` from `backend/`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/budge/contracts/export.py backend/src/budge/cli.py \
        backend/tests frontend/src/shared/api/contracts.ts
git commit -m "Generate the TypeScript contract, commit it, and fail on divergence"
```

---

### Task 4: The CI that the divergence check fails

There is no workflow in this repository. §11 makes «расхождение Pydantic и
TypeScript валит CI» a requirement, and it cannot be met by a check nothing
runs.

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: budge
          POSTGRES_PASSWORD: budge
          POSTGRES_DB: budge_test
        # 5434 matches compose.test.yaml and `support/db.py`'s default, so
        # the suite runs here with the same URL a developer uses locally.
        ports: ["5434:5432"]
        options: >-
          --health-cmd "pg_isready -U budge -d budge_test"
          --health-interval 1s
          --health-timeout 3s
          --health-retries 30

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install
        run: pip install -e '.[dev]'

      - name: Lint
        run: ruff check

      - name: Typecheck
        run: mypy

      - name: Test
        run: pytest -q

      # §7.6 and §11: the models and the TypeScript must agree. This step
      # writes nothing — it prints the diff and exits non-zero, so the
      # failure names what drifted.
      - name: Contracts
        run: budge export-types --check
```

- [ ] **Step 2: Run every step locally, in order**

```bash
cd backend
.venv/bin/python -m ruff check
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/budge export-types --check
```

All four must succeed. A workflow whose steps have never been run in order
is a workflow that fails on its first push for a reason unrelated to the
change that triggered it.

- [ ] **Step 3: Prove the contract step actually fails**

Temporarily add a field to `StageFrame`, run
`.venv/bin/budge export-types --check`, and confirm it exits 1 and
prints a diff naming the field. Then revert the field and confirm the check
passes again. Record the observed output in the task report — a CI step
that has never been seen to fail is a CI step nobody knows is wired up.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Run the suite and the contract check on every push"
```

- [ ] **Step 5:** Use superpowers:finishing-a-development-branch.
