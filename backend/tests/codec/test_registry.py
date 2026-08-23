"""The registry is a frozen literal, so its tests are about completeness and
injectivity — the two properties a hand-maintained table loses first."""

from typing import get_args

from budge.db.codec.registry import CLASSES_BY_WIRE_NAME, CURRENT_VERSION, WIRE_NAMES
from budge.domain.events import Event


def test_every_event_in_the_union_has_a_wire_name() -> None:
    assert set(WIRE_NAMES) == set(get_args(Event))


def test_no_two_events_share_a_wire_name() -> None:
    """A collision would make one of the two undecodable, and the failure
    would surface as the wrong class coming back out of a log, not as an
    error."""
    assert len(set(WIRE_NAMES.values())) == len(WIRE_NAMES)


def test_wire_names_are_not_derived_from_class_names() -> None:
    """A renamed class must not be a data migration. If these were equal,
    someone would eventually generate them and the rename would silently
    orphan every row of the old name."""
    assert all(name != cls.__name__ for cls, name in WIRE_NAMES.items())


def test_the_reverse_index_covers_the_registry() -> None:
    assert CLASSES_BY_WIRE_NAME == {name: cls for cls, name in WIRE_NAMES.items()}


def test_every_wire_name_starts_at_version_one() -> None:
    assert CURRENT_VERSION == dict.fromkeys(WIRE_NAMES.values(), 1)
