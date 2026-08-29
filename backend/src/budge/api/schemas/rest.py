"""REST bodies. Ruling 5's half of the transport split: everything up to
and including `StartMatch`.

Two models here name a player — `AddPlayerBody` and `AssignSecretBody` —
and neither is a violation of §7.4. They name the player being
*administered*: the operator adds four players to a match and assigns each
a secret, and none of those four is the caller. The caller is the one
holding the session cookie, and nothing in this module can change that.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from budge.api.schemas.frames import HostFrame


class Body(BaseModel):
    # Same reason as the WebSocket union's: an unexpected field must be
    # refused, not silently dropped — a client that thought it had said
    # something and got a 200 has been told the wrong thing.
    model_config = ConfigDict(frozen=True, extra="forbid")


class LoginBody(Body):
    password: str


class BoardBody(Body):
    width: int
    height: int


class SettingsBody(Body):
    """Defaults are §12's, and they live on `MatchSettings` — repeated here
    only as the field defaults a client may omit."""

    base_seconds: int = 60
    bonus_cap_seconds: int = 15
    pass_penalty_seconds: int = 3


class CreateMatchBody(Body):
    """No board validation here. Ruling 4: `validate_board` owns divisibility,
    the size bounds and the player count, and a Pydantic constraint repeating
    any of it would be a second source of truth that drifts."""

    board: BoardBody
    settings: SettingsBody = Field(default_factory=SettingsBody)
    player_count: int


class AddPlayerBody(Body):
    player_id: UUID
    name: str
    colour: str


class AssignSecretBody(Body):
    player_id: UUID
    category: UUID


class ResetMatchBody(Body):
    """§A.2: one flag, answering two questions.

    No default on purpose: "reset" is an irreversible action in front of a
    room, and a client that forgot to say which one it meant should get a
    422, not the most destructive option silently.
    """

    keep_roster: bool


class OutcomeBody(BaseModel):
    """What every command route answers with.

    The same four words the socket's ack uses, for the same reason: ruling
    5 splits the transports, not the vocabulary.
    """

    model_config = ConfigDict(frozen=True)

    outcome: Literal["accepted", "noop", "rejected", "failed"]
    reason: str | None = None
    message: str | None = None


class CreatedMatchBody(OutcomeBody):
    """A created match, and the one place its stage link is ever produced.

    Ruling 7 derives the stage token instead of storing it, so there is no
    row to read it back from later. Handing it over here, once, is the
    whole of its lifecycle — and it does not expire, so once is enough.
    """

    match_id: UUID
    stage_token: str


class PlayerSummaryBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    colour: str
    eliminated: bool


class MatchSummaryBody(BaseModel):
    """One row of §5.2's read model (ruling 14). Board and settings are
    deliberately absent — they live in `MatchCreated`, and the read model
    carries only what the admin list needs."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    status: str
    winner_id: UUID | None
    last_seq: int
    players: tuple[PlayerSummaryBody, ...]


class SnapshotBody(BaseModel):
    """§7.4's «снапшот для первичной загрузки».

    It carries the identical `HostFrame` the socket sends, built by the
    identical `project_host`: a console that loads and a console that
    reconnects must not have two shapes to handle.
    """

    model_config = ConfigDict(frozen=True)

    frame: HostFrame
    stage_token: str
