"""Runtime-level failures.

`Quarantined` is a state, not really an error: a match that reaches it
stops consuming its queue and refuses new commands until the process
restarts. Nothing is written to the log when it happens — the log is what
we still trust.
"""


class Quarantined(Exception):
    """This match is no longer being played by this process."""


class MatchAlreadyRunning(Exception):
    """The manager was asked to start a second loop for one match.

    §6 gives each match one sequential queue. The optimistic append would
    catch a second writer, but catching it is a failure path; not having
    one is the design.
    """
