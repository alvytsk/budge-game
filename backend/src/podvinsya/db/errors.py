"""Exceptions raised by the persistence layer.

One home for the whole layer's error surface: the codec's three live here
alongside the store's `ConcurrentModification` (Task 7) and the
repository's `MatchNotFound` (Task 10).
"""


class EventStreamCorrupt(Exception):
    """A log cannot be turned back into events.

    Every subclass is a *permanent* failure — retrying decodes the same
    bytes and fails the same way — which is what lets the runtime classify
    it as quarantine rather than retry.
    """


class UnknownEventType(EventStreamCorrupt):
    """`decode` was given a wire `type` absent from the registry."""


class UnknownSchemaVersion(EventStreamCorrupt):
    """`decode` was given a `schema_version` the upcaster chain cannot reach.

    Either it is newer than `CURRENT_VERSION` for that wire type, or an
    intermediate step is missing from the chain.
    """


class NaiveDatetime(EventStreamCorrupt):
    """A datetime reachable from an event has no `tzinfo`.

    There is no correct instant to recover, so the codec refuses rather than
    guessing a zone. Raised with a dotted path to the offending field.
    """
