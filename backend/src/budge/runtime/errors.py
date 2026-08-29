"""Runtime-level failures."""


class MatchAlreadyRunning(Exception):
    """The manager was asked to start a second loop for one match.

    §6 gives each match one sequential queue. The optimistic append would
    catch a second writer, but catching it is a failure path; not having
    one is the design.
    """


class ManagerShuttingDown(Exception):
    """`start` was called after `shutdown` had already begun.

    The manager accepts no new matches once it starts letting go of the
    ones it already has -- a `start` that raced `shutdown` and lost gets
    this instead of a runtime that would be torn down moments later.
    """
