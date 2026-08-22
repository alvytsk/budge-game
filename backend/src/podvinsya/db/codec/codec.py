"""Encode and decode between domain events and JSONB-ready payloads.

Serialization is a Pydantic `TypeAdapter` per event class, not hand-rolled
reflection: the fifteen dataclasses nest `Budgets`, `DealtCell`, a `Cell`
NamedTuple, a `frozenset`, UUID NewTypes, tuples and optional datetimes,
which is exactly where a hand-written walker accumulates quiet bugs.
Adapters are cached per class — building one per event during a replay
would be pure waste.

One invariant Pydantic will not enforce is handled here explicitly: every
datetime reachable from an event must be aware and UTC. Pydantic accepts a
naive datetime on both the Python side and the JSON side, so `normalize_utc`
enforces it structurally on `encode` and on `decode`.

Payload array order for set-valued fields is not part of the contract.
`DuelResolved.absorbed_cells` is a `frozenset`, and Pydantic dumps it in
set-iteration order; nothing reads these arrays positionally — reconciliation
compares `seq` and `type` only — and forcing an order by substituting a
sorted tuple makes Pydantic emit a serializer warning.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import TypeAdapter

from podvinsya.db.codec.registry import CLASSES_BY_WIRE_NAME, CURRENT_VERSION, WIRE_NAMES
from podvinsya.db.codec.upcasters import upcast_chain
from podvinsya.db.errors import NaiveDatetime, UnknownEventType
from podvinsya.domain.events import Event

# A manual dict rather than `functools.cache`: mypy strict rejects a
# `type[X] | type[Y] | ...` argument against `functools`'s `Hashable`-typed
# wrapper, which would force a `type: ignore` at every call site instead of
# one comment here.
_ADAPTERS: dict[type[Any], TypeAdapter[Any]] = {}


def _adapter_for(cls: type[Any]) -> TypeAdapter[Any]:
    adapter = _ADAPTERS.get(cls)
    if adapter is None:
        adapter = TypeAdapter(cls)
        _ADAPTERS[cls] = adapter
    return adapter


def _walk(value: Any, path: str) -> Any:
    """Every datetime reachable from `value` must be aware and UTC.

    The walk is structural — driven by `isinstance` on the actual value
    tree, not by a list of "fields known to carry a datetime" — so a new
    datetime field on any future event inherits the invariant without anyone
    remembering to add it here.

    A naive value has no correct instant to recover, so it is rejected. An
    aware-but-not-UTC value denotes the correct instant, so it is normalized
    rather than rejected.

    `frozenset` is walked and rebuilt as a frozenset: `DuelResolved` carries
    one, and returning any other container from here would make Pydantic
    serialize a value that does not match the field's declared type. `list`
    and `set` are deliberately not traversed silently — no event field uses
    one today, and falling through to the leaf case would quietly skip any
    datetime nested inside.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise NaiveDatetime(path)
        return value.astimezone(UTC)
    if is_dataclass(value) and not isinstance(value, type):
        updates = {
            f.name: _walk(getattr(value, f.name), f"{path}.{f.name}") for f in fields(value)
        }
        instance: Any = value
        return replace(instance, **updates)
    if isinstance(value, tuple):
        walked = tuple(_walk(item, f"{path}[{i}]") for i, item in enumerate(value))
        # A NamedTuple — `Cell` — must be rebuilt as itself. Returning a plain
        # tuple here would hand Pydantic a value that no longer matches the
        # field's declared type, and the loss would be silent.
        return type(value)(*walked) if hasattr(value, "_fields") else walked
    if isinstance(value, frozenset):
        return frozenset(_walk(item, f"{path}{{}}") for item in value)
    if isinstance(value, Mapping):
        return {key: _walk(item, f"{path}[{key!r}]") for key, item in value.items()}
    if isinstance(value, list | set):
        raise TypeError(
            f"{path}: codec cannot walk a {type(value).__name__} — no event field uses "
            "one today, and this walk must not silently skip whatever it might contain"
        )
    return value


def normalize_utc[T](value: T) -> T:
    """Typed facade over `_walk`, which is `Any`-in `Any`-out because it
    recurses through heterogeneous fields. `encode` and `decode` both want
    their input type back, not `Any`."""
    return cast(T, _walk(value, "$"))


def encode(event: Event) -> tuple[str, int, dict[str, Any]]:
    wire_type = WIRE_NAMES[type(event)]
    normalized = normalize_utc(event)
    payload: dict[str, Any] = _adapter_for(type(event)).dump_python(normalized, mode="json")
    return wire_type, CURRENT_VERSION[wire_type], payload


def decode(wire_type: str, schema_version: int, payload: Mapping[str, Any]) -> Event:
    cls = CLASSES_BY_WIRE_NAME.get(wire_type)
    if cls is None:
        raise UnknownEventType(wire_type)
    upcast = upcast_chain(wire_type, schema_version)  # raises UnknownSchemaVersion
    event: Event = _adapter_for(cls).validate_python(upcast(dict(payload)))
    return normalize_utc(event)
