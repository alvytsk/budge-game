"""What a client is allowed to say, and what it hears back.

The rule this module exists to hold is §7.4's: «Клиент **никогда не
передаёт, кто он**. Принципал выводится из аутентифицированной сессии.» So
no model here carries an actor, a role, a seat or a sender. There is one
operator, the transport says which one, and a body that named a player
would make «названный актор действительно участник» a check that can be
passed by lying.

`ExpireTimer` is deliberately absent from the union. It is the server's own
command — §4.3's scheduler issues it and §6.2's loop honours it — and a
client able to name one could resolve a duel by claiming a deadline fired.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from podvinsya.domain.actions import (
    Command,
    DeclareAttack,
    JudgeCorrect,
    JudgePass,
    PauseDuel,
    ResumeDuel,
    StartDuel,
    UndoLastJudgement,
)
from podvinsya.domain.ids import GroupId


class Inbound(BaseModel):
    # `extra="forbid"`: a client sending `{"type": "judge_correct",
    # "player": "..."}` must be refused, not quietly served with the field
    # ignored. A silently-dropped actor field is the shape §7.4 warns
    # about — it looks accepted from the client's side.
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeclareAttackCommand(Inbound):
    type: Literal["declare_attack"] = "declare_attack"
    attacking_group: UUID
    defending_group: UUID

    def to_domain(self) -> Command:
        return DeclareAttack(
            attacking_group=GroupId(self.attacking_group),
            defending_group=GroupId(self.defending_group),
        )


class StartDuelCommand(Inbound):
    type: Literal["start_duel"] = "start_duel"

    def to_domain(self) -> Command:
        return StartDuel()


class JudgeCorrectCommand(Inbound):
    type: Literal["judge_correct"] = "judge_correct"

    def to_domain(self) -> Command:
        return JudgeCorrect()


class JudgePassCommand(Inbound):
    type: Literal["judge_pass"] = "judge_pass"

    def to_domain(self) -> Command:
        return JudgePass()


class PauseDuelCommand(Inbound):
    type: Literal["pause_duel"] = "pause_duel"

    def to_domain(self) -> Command:
        return PauseDuel()


class ResumeDuelCommand(Inbound):
    type: Literal["resume_duel"] = "resume_duel"

    def to_domain(self) -> Command:
        return ResumeDuel()


class UndoLastJudgementCommand(Inbound):
    type: Literal["undo_last_judgement"] = "undo_last_judgement"

    def to_domain(self) -> Command:
        return UndoLastJudgement()


LiveCommand = Annotated[
    DeclareAttackCommand
    | StartDuelCommand
    | JudgeCorrectCommand
    | JudgePassCommand
    | PauseDuelCommand
    | ResumeDuelCommand
    | UndoLastJudgementCommand,
    Field(discriminator="type"),
]


class Envelope(Inbound):
    """One command, plus the client's own correlation id.

    `correlation_id` is echoed into the acknowledgement and used for
    nothing else. §5.1 makes `operation_id` the server's job without
    exception — `QueuedCommand.issue` mints it — and repeating a client's
    value there would let one client's retry make another's ambiguous
    commit reconcile as already-written.
    """

    correlation_id: str | None = None
    command: LiveCommand


class Ack(BaseModel):
    """What the operator hears back about one command.

    It carries the outcome, never the state: §7.2 puts the whole state in
    the frame that follows, and an ack that also carried a state would give
    the console two sources for one truth, arriving in an order the network
    decides.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["ack"] = "ack"
    correlation_id: str | None
    outcome: Literal["accepted", "noop", "rejected", "failed", "malformed"]
    # The domain's own `RejectionReason` value for a rejection (ruling 4),
    # the `RuntimeCode` value for a failure, and `None` otherwise.
    reason: str | None = None
    message: str | None = None
