"""The two frames §7.1 describes, as the types plan 5 generates from.

The design constraint here is not "filter the answers out on the way".
`StageFrame`'s type graph has nowhere to put an answer and nowhere to put
the name of an unrevealed category: `StageGroupFrame.category` is a union
of «named» and «hidden», and «hidden» has no fields at all. A leak would
have to be a new field somebody added, which is what
`test_a_stage_frame_has_no_field_anywhere_that_could_hold_an_answer`
watches for — and what ruling 1 removes one layer earlier still, by never
letting the projection *ask* for what the stage may not show.

Every model here is frozen. A frame is a value that was true at one `seq`;
a mutable one invites a caller to patch it and send it on as if the server
had said it.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from budge.domain.state import DuelPhase, MatchStatus


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class CellFrame(Frozen):
    col: int
    row: int


class BoardFrame(Frozen):
    width: int
    height: int


class PlayerFrame(Frozen):
    id: UUID
    name: str
    colour: str
    eliminated: bool


class TimingFrame(Frozen):
    """§7.3, in one model. The server sends this and never a tick.

    `remaining_ms` carries *both* players, always. §7.3 names both, and a
    frame carrying only the answering player's remainder would leave the
    idle player's timer to be guessed at by a client that has no way to
    know when it last moved.
    """

    remaining_ms: dict[UUID, int]
    answering: UUID
    anchor: datetime | None
    paused: bool
    deadline_at: datetime | None


class ResolutionFrame(Frozen):
    """Ruling 13: what §9.1's third beat needs to animate a capture.

    Which cells moved, and between whom. No category: the beat is about
    the merge, and a frame with nothing optional in it is a frame that is
    easier to keep leak-proof.
    """

    winner: UUID
    loser: UUID
    surviving_group: UUID
    absorbed_group: UUID
    absorbed_cells: tuple[CellFrame, ...]


class NamedCategory(Frozen):
    """A category the room is allowed to read, and the library could name."""

    kind: Literal["named"] = "named"
    name: str


class HiddenCategory(Frozen):
    """Ruling 3: «секрет, ещё не раскрыт» and «имени нет», one variant.

    No fields — not even the category id. The id leaks no name, but a field
    that exists is a field a later change can fill; an empty model cannot
    be filled by accident. The word «Секрет» is the front-end's to render.
    """

    kind: Literal["hidden"] = "hidden"


StageCategory = NamedCategory | HiddenCategory


class HostCategory(Frozen):
    """Ruling 3's other half. The operator always sees the id, and a `None`
    name is a content defect made visible rather than silent."""

    id: UUID
    name: str | None


class StageGroupFrame(Frozen):
    id: UUID
    owner: UUID
    category: StageCategory
    cells: tuple[CellFrame, ...]
    revealed: bool


class HostGroupFrame(Frozen):
    id: UUID
    owner: UUID
    category: HostCategory
    cells: tuple[CellFrame, ...]
    revealed: bool


class StageDuelFrame(Frozen):
    """The whole image pack rides here, and no answer does.

    §3.5 draws the order at *declaration*, and §9.1's first beat preloads
    it, so the ids are on the frame from the moment the attack is declared.
    An id is not an answer, and the browser cache holding an image that has
    not been drawn reveals nothing (§7.1).
    """

    attacker: UUID
    defender: UUID
    attacking_group: UUID
    defending_group: UUID
    category: StageCategory
    image_order: tuple[UUID, ...]
    index: int
    phase: DuelPhase
    timing: TimingFrame


class HostDuelFrame(Frozen):
    """Everything the stage duel frame has, plus the two things §7.1 gives
    the operator alone: the current image's answer, and how many are left."""

    attacker: UUID
    defender: UUID
    attacking_group: UUID
    defending_group: UUID
    category: HostCategory
    image_order: tuple[UUID, ...]
    index: int
    image_count: int
    current_answer: str | None
    phase: DuelPhase
    timing: TimingFrame


class StageFrame(Frozen):
    kind: Literal["stage"] = "stage"
    match_id: UUID
    seq: int
    server_now: datetime
    status: MatchStatus
    board: BoardFrame
    players: tuple[PlayerFrame, ...]
    current_player: UUID | None
    round_no: int
    groups: tuple[StageGroupFrame, ...]
    duel: StageDuelFrame | None
    winner: UUID | None
    last_event_types: tuple[str, ...]
    resolution: ResolutionFrame | None


class HostFrame(Frozen):
    kind: Literal["host"] = "host"
    match_id: UUID
    seq: int
    server_now: datetime
    status: MatchStatus
    board: BoardFrame
    players: tuple[PlayerFrame, ...]
    current_player: UUID | None
    round_no: int
    groups: tuple[HostGroupFrame, ...]
    duel: HostDuelFrame | None
    winner: UUID | None
    last_event_types: tuple[str, ...]
    resolution: ResolutionFrame | None
    # §9.2: «Правило смежности не проверяется, а делается невозможным.» The
    # console greys out what is not here rather than sending an attack and
    # learning from the rejection.
    legal_attacks: dict[UUID, tuple[UUID, ...]]
