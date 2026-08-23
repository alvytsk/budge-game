"""The contract document, and the one thing an explicit root list can get
wrong."""

import inspect
from typing import Any

from pydantic import BaseModel

from budge.api.schemas import commands, frames, library, media, rest
from budge.contracts.schema import ROOTS, contract_schema

SCHEMA_MODULES = (frames, commands, library, media, rest)

# Base classes, not contract members: they carry configuration and no
# fields, and emitting them would put two empty interfaces in the artifact
# that no wire message is ever an instance of.
BASE_MODELS = {"Frozen", "Inbound", "Body", "LibraryBody", "Response"}


def models_in(module: Any) -> set[str]:
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
    declared: set[str] = set().union(*(models_in(module) for module in SCHEMA_MODULES))
    assert not (declared - exported)


def test_the_base_model_exemptions_still_exist() -> None:
    """A name left in `BASE_MODELS` after the class was deleted would
    silently exempt nothing, and a typo would silently exempt a real model.

    Kills on: either — the exemption list must name classes that exist and
    that genuinely have no fields."""
    found: set[str] = set()
    for module in SCHEMA_MODULES:
        for name, member in inspect.getmembers(module, inspect.isclass):
            if name not in BASE_MODELS or member.__module__ != module.__name__:
                continue
            assert issubclass(member, BaseModel)
            found.add(name)
            assert not member.model_fields
    assert found == BASE_MODELS


def test_every_root_is_a_model_in_a_schema_module() -> None:
    """Kills on: exporting a model that lives outside `api/schemas/` — a
    domain dataclass or a service type — which would put a shape on the
    wire contract that no route or socket ever sends."""
    declared: set[str] = set().union(*(models_in(module) for module in SCHEMA_MODULES))
    assert {model.__name__ for model, _mode in ROOTS} <= declared
