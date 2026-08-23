"""One `CommandOutcome`, two audiences, one mapping.

REST and the WebSocket both funnel every command through the same gateway
(ruling 5), so they must also agree on what each outcome *means* — a
rejection that is a 409 over REST and an "accepted" over the socket would
be one bug per transport rather than one behaviour.

Written as a `match` over the union rather than a dictionary lookup: a new
`CommandOutcome` member becomes a type error here, at the one place that
has to have an opinion, instead of silently falling through to a 200.
"""

from dataclasses import dataclass

from podvinsya.api.schemas.commands import Ack
from podvinsya.runtime.origins import Accepted, CommandOutcome, Failed, NoOp, Rejected
from podvinsya.services.ports import RuntimeCode

# §6.3 calls a content shortfall «обычный отказ, не авария»: the operator is
# told plainly and the match stays on the air, so it maps alongside a domain
# rejection rather than alongside a fault. The other three take a match, or
# the process, off the air, and a load balancer must be able to tell.
_FAILURE_STATUS = {
    RuntimeCode.CONTENT_UNAVAILABLE: 409,
    RuntimeCode.QUARANTINED: 503,
    RuntimeCode.DATABASE_UNAVAILABLE: 503,
    RuntimeCode.INTERNAL: 500,
}


@dataclass(frozen=True, slots=True)
class HttpOutcome:
    status: int
    body: dict[str, str | None]


def http_outcome(outcome: CommandOutcome) -> HttpOutcome:
    """The REST answer for one outcome.

    A no-op is a 200, not a 204 and not a 409: §6.2 calls it a benign race
    — a stale expiry, a duplicate — and the caller's view of the world is
    already correct, which is what a 200 says.
    """
    match outcome:
        case Accepted():
            return HttpOutcome(200, {"outcome": "accepted"})
        case NoOp():
            return HttpOutcome(200, {"outcome": "noop"})
        case Rejected(reason):
            # Ruling 4: the domain's own string, not an API-local
            # translation of it. A second vocabulary here would be a second
            # source of truth that drifts the first time a reason is added.
            return HttpOutcome(409, {"outcome": "rejected", "reason": reason.value})
        case Failed(code, message):
            return HttpOutcome(
                _FAILURE_STATUS[code],
                {"outcome": "failed", "reason": code.value, "message": message},
            )


def ack_for(outcome: CommandOutcome, *, correlation_id: str | None) -> Ack:
    """The socket's answer for the same outcome, carrying the same strings."""
    match outcome:
        case Accepted():
            return Ack(correlation_id=correlation_id, outcome="accepted")
        case NoOp():
            return Ack(correlation_id=correlation_id, outcome="noop")
        case Rejected(reason):
            return Ack(correlation_id=correlation_id, outcome="rejected", reason=reason.value)
        case Failed(code, message):
            return Ack(
                correlation_id=correlation_id,
                outcome="failed",
                reason=code.value,
                message=message,
            )


def malformed_ack(correlation_id: str | None, message: str) -> Ack:
    """A frame that never became a command.

    Its own outcome rather than a `rejected`: a rejection is the domain
    refusing a legal command, and conflating "your JSON was wrong" with
    "that move is not allowed" would make the console show the operator a
    rules message for a client bug.
    """
    return Ack(correlation_id=correlation_id, outcome="malformed", message=message)
