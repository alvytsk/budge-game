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


class InvalidPayload(EventStreamCorrupt):
    """A payload does not validate against its event class.

    A missing field, a wrong type, or a payload shaped for a different wire
    type all land here — the likeliest real-world corruption, since it is
    what a forgotten upcaster looks like. Raised with the wire type, the
    schema version, and the underlying Pydantic message, so an operator can
    tell which row is bad and why.
    """


class ConcurrentModification(Exception):
    """`append`'s optimistic UPDATE matched zero rows.

    Raised with `(match_id, expected_last_seq)`. Someone else advanced this
    match's `last_seq` past what this attempt's `decide()` saw. A retry
    would append events decided against state that is no longer current —
    what the runtime should do about that (quarantine, re-decide, something
    else) is a policy question this layer leaves to the runtime plan.
    """


class MatchNotFound(Exception):
    """`load` was given a match id with no log behind it.

    Distinct from an empty or corrupt stream: a match that was never created
    is a caller mistake, not a data problem.
    """
