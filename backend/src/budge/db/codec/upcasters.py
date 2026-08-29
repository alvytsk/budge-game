"""The upcaster chain: composes a payload forward from an old schema version
to the current one before Pydantic validates it.

`UPCASTERS` is empty at v1 — nothing has been renamed, retyped or removed
from any event yet, so there is nothing to compose. `_compose` is factored
out of `upcast_chain` so it can be exercised against a test-local synthetic
registry: the production tables give it nothing to do, and a test that ran
it against them would prove nothing about the loop or either guard.
"""

from collections.abc import Callable, Mapping
from typing import Any

from budge.db.codec.registry import CURRENT_VERSION
from budge.db.errors import UnknownSchemaVersion

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]

# (wire_type, from_version) -> transform producing from_version + 1.
UPCASTERS: Mapping[tuple[str, int], Upcaster] = {}


def _compose(
    upcasters: Mapping[tuple[str, int], Upcaster],
    current_version: Mapping[str, int],
    wire_type: str,
    from_version: int,
) -> Upcaster:
    """Build the transform for `wire_type` from `from_version` to current,
    without touching the module-level tables."""
    target = current_version[wire_type]
    if from_version > target:
        raise UnknownSchemaVersion(
            f"{wire_type} schema_version {from_version} is newer than current {target}"
        )

    def _chain(payload: dict[str, Any]) -> dict[str, Any]:
        version = from_version
        while version < target:
            step = upcasters.get((wire_type, version))
            if step is None:
                raise UnknownSchemaVersion(
                    f"no upcaster registered for {wire_type} v{version} -> v{version + 1}"
                )
            payload = step(payload)
            version += 1
        return payload

    return _chain


def upcast_chain(wire_type: str, from_version: int) -> Upcaster:
    """Compose forward until the payload matches the current version."""
    return _compose(UPCASTERS, CURRENT_VERSION, wire_type, from_version)
