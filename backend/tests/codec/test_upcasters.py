"""The production tables are empty at v1, so every test here drives
`_compose` against a synthetic registry. Running the loop against the real
tables would prove nothing about the loop, the missing-step guard, or the
above-current guard — there would be nothing for it to do."""

from typing import Any

import pytest

from budge.db.codec.upcasters import UPCASTERS, Upcaster, _compose, upcast_chain
from budge.db.errors import UnknownSchemaVersion


def _rename(old: str, new: str) -> Upcaster:
    def step(payload: dict[str, Any]) -> dict[str, Any]:
        return {new if key == old else key: value for key, value in payload.items()}

    return step


SYNTHETIC: dict[tuple[str, int], Upcaster] = {
    ("duel.started", 1): _rename("at", "when"),
    ("duel.started", 2): _rename("when", "anchor"),
}
SYNTHETIC_CURRENT = {"duel.started": 3}


def test_the_chain_composes_every_step_in_order() -> None:
    upcast = _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 1)
    assert upcast({"at": "2026-08-22T12:00:00Z"}) == {"anchor": "2026-08-22T12:00:00Z"}


def test_a_payload_already_current_passes_through_untouched() -> None:
    upcast = _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 3)
    assert upcast({"anchor": "x"}) == {"anchor": "x"}


def test_a_partial_chain_starts_where_the_payload_is() -> None:
    upcast = _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 2)
    assert upcast({"when": "x"}) == {"anchor": "x"}


def test_a_missing_step_is_refused_rather_than_skipped() -> None:
    upcast = _compose({("duel.started", 2): _rename("when", "anchor")},
                      SYNTHETIC_CURRENT, "duel.started", 1)
    with pytest.raises(UnknownSchemaVersion):
        upcast({"at": "x"})


def test_a_version_newer_than_current_is_refused_immediately() -> None:
    """Refused when the chain is built, not when it runs: a log written by a
    newer deployment is a deployment problem, and reading it as if it were
    current would corrupt a match silently."""
    with pytest.raises(UnknownSchemaVersion):
        _compose(SYNTHETIC, SYNTHETIC_CURRENT, "duel.started", 4)


def test_the_production_registry_is_empty_at_version_one() -> None:
    assert UPCASTERS == {}


def test_the_production_chain_is_an_identity_today() -> None:
    assert upcast_chain("duel.started", 1)({"anchor": "x"}) == {"anchor": "x"}
