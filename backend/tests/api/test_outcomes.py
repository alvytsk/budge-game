"""One outcome, two transports, one answer.

Ruling 5 sends setup over REST and live judging over the socket, both
through the same gateway. These tests are what keeps the two from
disagreeing about what happened.
"""

import pytest

from budge.api.outcomes import ack_for, http_outcome, malformed_ack
from budge.domain.errors import RejectionReason
from budge.runtime.origins import Accepted, CommandOutcome, Failed, NoOp, Rejected
from budge.services.ports import RuntimeCode

EVERY_OUTCOME: list[CommandOutcome] = [
    Accepted(()),
    NoOp(),
    *[Rejected(reason) for reason in RejectionReason],
    *[Failed(code, "why") for code in RuntimeCode],
]


@pytest.mark.parametrize("outcome", EVERY_OUTCOME, ids=repr)
def test_every_outcome_maps_to_a_status(outcome: CommandOutcome) -> None:
    """Every member of the union and every runtime code, exhaustively.

    Kills on: a `case _: return 200` fallback, which would turn a
    quarantined match into a success the operator keeps pressing buttons
    against."""
    result = http_outcome(outcome)
    assert result.status in {200, 409, 500, 503}
    assert result.body["outcome"] in {"accepted", "noop", "rejected", "failed"}


@pytest.mark.parametrize("reason", list(RejectionReason), ids=lambda r: r.value)
def test_a_rejection_carries_the_domain_s_own_reason_string(reason: RejectionReason) -> None:
    """Ruling 4: `decide` owns validity, and its reason is what the caller
    hears. Kills on: translating reasons into API-local strings, which is
    ruling 4's second source of truth arriving through another door — the
    two vocabularies would drift the first time a reason was added."""
    result = http_outcome(Rejected(reason))
    assert result.status == 409
    assert result.body["reason"] == reason.value

    ack = ack_for(Rejected(reason), correlation_id="c1")
    assert ack.outcome == "rejected"
    assert ack.reason == reason.value


def test_an_accepted_command_and_a_no_op_are_both_two_hundred() -> None:
    """§6.2 calls a no-op a benign race — a stale expiry, a duplicate. The
    caller's view of the world is already right, which is what a 200 says.

    Kills on: mapping `NoOp` to 409, which would show the operator an
    error for something the server did to itself."""
    assert http_outcome(Accepted(())).status == 200
    assert http_outcome(NoOp()).status == 200
    assert http_outcome(NoOp()).body["outcome"] == "noop"


def test_a_content_shortfall_is_an_ordinary_refusal() -> None:
    """§6.3: «обычный отказ, не авария», and §8 makes image exhaustion a
    content defect rather than a domain transition.

    Kills on: mapping it to 503, which would tell a load balancer the node
    was unhealthy because a category ran out of pictures."""
    result = http_outcome(Failed(RuntimeCode.CONTENT_UNAVAILABLE, "no images left"))
    assert result.status == 409
    assert result.body["reason"] == "content_unavailable"


def test_a_quarantined_match_is_a_503_and_an_internal_error_is_a_500() -> None:
    """Kills on: collapsing the two, which would make «this match is off
    the air, the rest of the process is fine» indistinguishable from «this
    process has a bug» in the one place an operator looks."""
    assert http_outcome(Failed(RuntimeCode.QUARANTINED, "gone")).status == 503
    assert http_outcome(Failed(RuntimeCode.DATABASE_UNAVAILABLE, "gone")).status == 503
    assert http_outcome(Failed(RuntimeCode.INTERNAL, "bug")).status == 500


def test_a_failure_message_reaches_the_caller() -> None:
    """Kills on: dropping the message, which leaves an operator staring at
    a bare 503 with nothing to tell anyone."""
    result = http_outcome(Failed(RuntimeCode.INTERNAL, "the materialiser exploded"))
    assert result.body["message"] == "the materialiser exploded"


@pytest.mark.parametrize("outcome", EVERY_OUTCOME, ids=repr)
def test_the_two_transports_agree_on_what_happened(outcome: CommandOutcome) -> None:
    """The reason ruling 5 is safe: one gateway, one vocabulary.

    Kills on: an ack whose `outcome` disagrees with the HTTP body's —
    a rejection over REST and an acceptance over the socket would be one
    bug per transport rather than one behaviour."""
    assert ack_for(outcome, correlation_id=None).outcome == http_outcome(outcome).body["outcome"]


def test_an_acknowledgement_echoes_the_correlation_id() -> None:
    assert ack_for(NoOp(), correlation_id="console-9").correlation_id == "console-9"
    assert ack_for(NoOp(), correlation_id=None).correlation_id is None


def test_a_malformed_frame_is_its_own_outcome() -> None:
    """Kills on: reusing `rejected`, which would make the console show the
    operator a rules message — "not your turn", "no duel" — for what is
    actually a client bug in its own JSON."""
    ack = malformed_ack("c2", "command.type: unknown variant")
    assert ack.outcome == "malformed"
    assert ack.reason is None
    assert ack.correlation_id == "c2"
    assert ack.message is not None
