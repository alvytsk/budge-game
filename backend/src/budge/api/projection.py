"""One immutable `MatchState` into one of exactly two frames.

Ruling 1 is the whole design of `project_stage`: it collects the category
ids of *revealed groups only*, and passes `images=frozenset()`. It never
asks for a single image answer and never asks for an unrevealed category's
name — so there is nothing in the function's local scope to leak, one layer
earlier than the frame. The §11 whole-tree test stays as the backstop; the
test that reads `RecordingContentDirectory.asked_categories` is the stronger
statement.

Both functions are `async` because both await the directory. That is
precisely why ruling 9 puts them in each subscriber's own writer task and
not in `Broadcaster.publish`, which §6.1 forbids to await at all.
"""

from collections.abc import Sequence

from datetime import datetime

from budge.api.schemas.frames import (
    BoardFrame,
    CellFrame,
    HiddenCategory,
    HostCategory,
    HostDuelFrame,
    HostFrame,
    HostGroupFrame,
    NamedCategory,
    PlayerFrame,
    ResolutionFrame,
    StageCategory,
    StageDuelFrame,
    StageFrame,
    StageGroupFrame,
    TimingFrame,
)
from budge.db.codec.registry import WIRE_NAMES
from budge.domain.board import Cell
from budge.domain.events import DuelResolved, Event
from budge.domain.ids import CategoryId, GroupId, ImageId, PlayerId
from budge.domain.rules import legal_targets
from budge.domain.state import Duel, Group, MatchState, MatchStatus
from budge.domain.timing import deadline_of
from budge.services.ports import ContentDescription, ContentDirectory


def _cells(cells: frozenset[Cell]) -> tuple[CellFrame, ...]:
    """Sorted, because a `frozenset` has no order and a frame that reordered
    its own cells between two otherwise identical states would make every
    client-side diff spurious."""
    return tuple(CellFrame(col=cell.col, row=cell.row) for cell in sorted(cells))


def _players(state: MatchState) -> tuple[PlayerFrame, ...]:
    return tuple(
        PlayerFrame(id=p.id, name=p.name, colour=p.colour, eliminated=p.eliminated)
        for p in state.players
    )


def _board(state: MatchState) -> BoardFrame:
    return BoardFrame(width=state.board.width, height=state.board.height)


def _current_player(state: MatchState) -> PlayerId | None:
    """`None` outside a running match: `current_player()` indexes
    `turn_order`, which is empty until `MatchStarted`."""
    if state.status is not MatchStatus.RUNNING or not state.turn_order:
        return None
    return state.current_player()


def _timing(duel: Duel) -> TimingFrame:
    """§7.3: both remainders, the anchor, the absolute deadline, and paused.

    `deadline_of` returns `None` for a paused duel — there is no anchor to
    add a remainder to — and that is exactly what the client needs to see:
    a paused timer with a deadline would be a timer the room watches run
    out while nothing is happening.
    """
    return TimingFrame(
        remaining_ms={player: duel.budgets.get(player) for player in duel.budgets.players()},
        answering=duel.answering,
        anchor=duel.anchor,
        paused=duel.paused,
        deadline_at=deadline_of(duel),
    )


def _wire_names(events: Sequence[Event]) -> tuple[str, ...]:
    """Ruling 13: the frozen wire names from plan 2's registry, not
    `type(event).__name__`. A class renamed in a refactor is deliberately
    not a wire change, and reading `__name__` here would make it one."""
    return tuple(WIRE_NAMES[type(event)] for event in events)


def _resolution(events: Sequence[Event]) -> ResolutionFrame | None:
    """Ruling 13: §9.1's third beat needs to know a capture happened and
    which cells moved, and §7.2 forbids the client from reconstructing that
    by diffing against a frame it may never have received."""
    for event in events:
        if isinstance(event, DuelResolved):
            return ResolutionFrame(
                winner=event.winner,
                loser=event.loser,
                surviving_group=event.surviving_group,
                absorbed_group=event.absorbed_group,
                absorbed_cells=_cells(event.absorbed_cells),
            )
    return None


def _stage_category(group: Group, described: ContentDescription) -> StageCategory:
    """Ruling 3: two variants, and `hidden` covers both «секрет, ещё не
    раскрыт» and «имени нет».

    A revealed group whose category the library cannot name reads as a
    secret on the big screen. That is the visible failure ruling 3 chose
    over a silent one — an operator watching the stage sees it immediately.
    """
    if not group.revealed:
        return HiddenCategory()
    name = described.category_names.get(group.category)
    return NamedCategory(name=name) if name is not None else HiddenCategory()


def _host_category(category: CategoryId, described: ContentDescription) -> HostCategory:
    return HostCategory(id=category, name=described.category_names.get(category))


async def project_stage(
    state: MatchState,
    *,
    now: datetime,
    events: Sequence[Event],
    directory: ContentDirectory,
) -> StageFrame:
    revealed_categories = frozenset(
        group.category for group in state.groups.values() if group.revealed
    )
    # `images=frozenset()`, always. Ruling 1: the stage projection never
    # asks for an image answer, so no answer is ever in this function's
    # scope to be filtered out of a frame later.
    described = await directory.describe(categories=revealed_categories, images=frozenset())

    duel = state.duel
    stage_duel: StageDuelFrame | None = None
    if duel is not None:
        # The defender's group, whose category `AttackDeclared` revealed
        # (§3.5). Reading the name through that group keeps the duel's
        # category under exactly the same rule as every other group's.
        defending = state.groups.get(duel.defending_group)
        stage_duel = StageDuelFrame(
            attacker=duel.attacker,
            defender=duel.defender,
            attacking_group=duel.attacking_group,
            defending_group=duel.defending_group,
            category=(
                _stage_category(defending, described) if defending is not None else HiddenCategory()
            ),
            image_order=tuple(duel.image_order),
            index=duel.index,
            phase=duel.phase,
            timing=_timing(duel),
        )

    return StageFrame(
        match_id=state.id,
        seq=state.seq,
        server_now=now,
        status=state.status,
        board=_board(state),
        players=_players(state),
        current_player=_current_player(state),
        round_no=state.round_no,
        groups=tuple(
            StageGroupFrame(
                id=group.id,
                owner=group.owner,
                category=_stage_category(group, described),
                cells=_cells(group.cells),
                revealed=group.revealed,
            )
            for group in state.groups.values()
        ),
        duel=stage_duel,
        winner=state.winner,
        last_event_types=_wire_names(events),
        resolution=_resolution(events),
    )


def _legal_attacks(state: MatchState) -> dict[GroupId, tuple[GroupId, ...]]:
    """§9.2, built with the domain's own `legal_targets`.

    Restricted to the current player's groups, and empty while a duel is in
    flight — `decide` refuses `DeclareAttack` outright then, and offering
    the console a target it would be refused for is worse than offering none.

    Recomputing adjacency here instead would be a second source of truth
    against `_declare_attack`, and the two would drift the first time the
    board grew a diagonal.
    """
    if state.status is not MatchStatus.RUNNING or state.duel is not None:
        return {}
    current = _current_player(state)
    if current is None:
        return {}
    return {
        group.id: tuple(sorted(legal_targets(state, group.id)))
        for group in state.groups.values()
        if group.owner == current
    }


async def project_host(
    state: MatchState,
    *,
    now: datetime,
    events: Sequence[Event],
    directory: ContentDirectory,
) -> HostFrame:
    """§7.1: «Пульт получает всё.»

    Every group's category name, including the unrevealed secrets, and the
    correct answer to the *current* image — one image, not the pack. The
    operator reads one answer at a time (§9.2), and a frame carrying sixty
    would put fifty-nine answers on the wire for every judgement.
    """
    duel = state.duel
    categories = frozenset(group.category for group in state.groups.values())
    images: frozenset[ImageId] = frozenset()
    if duel is not None:
        categories |= {duel.category}
        if 0 <= duel.index < len(duel.image_order):
            images = frozenset({duel.image_order[duel.index]})
    described = await directory.describe(categories=categories, images=images)

    host_duel: HostDuelFrame | None = None
    if duel is not None:
        current_image = (
            duel.image_order[duel.index] if 0 <= duel.index < len(duel.image_order) else None
        )
        host_duel = HostDuelFrame(
            attacker=duel.attacker,
            defender=duel.defender,
            attacking_group=duel.attacking_group,
            defending_group=duel.defending_group,
            category=_host_category(duel.category, described),
            image_order=tuple(duel.image_order),
            index=duel.index,
            image_count=len(duel.image_order),
            current_answer=(
                described.image_answers.get(current_image) if current_image is not None else None
            ),
            phase=duel.phase,
            timing=_timing(duel),
        )

    return HostFrame(
        match_id=state.id,
        seq=state.seq,
        server_now=now,
        status=state.status,
        board=_board(state),
        players=_players(state),
        player_count=state.player_count,
        current_player=_current_player(state),
        round_no=state.round_no,
        groups=tuple(
            HostGroupFrame(
                id=group.id,
                owner=group.owner,
                category=_host_category(group.category, described),
                cells=_cells(group.cells),
                revealed=group.revealed,
            )
            for group in state.groups.values()
        ),
        duel=host_duel,
        winner=state.winner,
        last_event_types=_wire_names(events),
        resolution=_resolution(events),
        legal_attacks=_legal_attacks(state),
    )
