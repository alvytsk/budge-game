"""Runtime-level failures."""


class MatchAlreadyRunning(Exception):
    """The manager was asked to start a second loop for one match.

    §6 gives each match one sequential queue. The optimistic append would
    catch a second writer, but catching it is a failure path; not having
    one is the design.
    """
