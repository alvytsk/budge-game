"""The two projections, and §11's «Проекции» row.

Every state here is built by driving `decide`/`evolve` through the domain
suite's own builders, never assembled by hand: a frame projected from a
state that could not occur proves nothing about the frames the room sees.

The leak tests come in two strengths. The §11 one walks the *whole* dumped
frame — every string at every depth — and is the backstop. The ruling-1 one
reads what the projection *asked* the directory for, and is stronger: it
fails on a projection that requested an answer and filtered it out again,
which the first would let through.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from domain.conftest import (
    BASE_TIME,
    apply,
    build_dealt_state,
    build_declared_state,
    build_duel_state,
    build_running_state,
)
from podvinsya.api.projection import project_host, project_stage
from podvinsya.api.schemas.frames import HiddenCategory, NamedCategory
from podvinsya.domain.actions import JudgePass, PauseDuel
from podvinsya.domain.context import DecisionContext
from podvinsya.domain.decide import decide
from podvinsya.domain.events import Event
from podvinsya.domain.evolve import fold
from podvinsya.domain.ids import CategoryId, GroupId, ImageId
from podvinsya.domain.rules import legal_targets
from podvinsya.domain.state import MatchState
from podvinsya.domain.timing import deadline_of
from support.content import OverAnsweringContentDirectory, RecordingContentDirectory
from support.walk import every_string

NOW = datetime(2026, 8, 23, 18, 30, tzinfo=UTC)

# Distinct on purpose: the whole-tree walk searches for these strings, and a
# name that happened to be a substring of a colour or a status would make a
# passing test meaningless.
ANSWER_PREFIX = "ОТВЕТ-НА-КАРТИНКУ-"
NAME_PREFIX = "НАЗВАНИЕ-КАТЕГОРИИ-"


def content_of(state: MatchState) -> tuple[dict[CategoryId, str], dict[ImageId, str]]:
    """A name for every category in the state and an answer for every image
    in its duel.

    Naming everything is what makes the stage's silence load-bearing: a
    directory that happened not to know an unrevealed category's name would
    let a leaking projection pass for the wrong reason.
    """
    names = {
        group.category: f"{NAME_PREFIX}{index}"
        for index, group in enumerate(state.groups.values())
    }
    answers: dict[ImageId, str] = {}
    if state.duel is not None:
        names.setdefault(state.duel.category, f"{NAME_PREFIX}duel")
        answers = {
            image: f"{ANSWER_PREFIX}{index}"
            for index, image in enumerate(state.duel.image_order)
        }
    return names, answers


def directory_for(state: MatchState) -> RecordingContentDirectory:
    """An honest directory: it answers only what it was asked."""
    names, answers = content_of(state)
    return RecordingContentDirectory(names, answers)


def over_answering_directory_for(state: MatchState) -> OverAnsweringContentDirectory:
    """A directory that hands back every name and every answer it has,
    whatever the projection asked for.

    Without this, §11's whole-tree tests below cannot fail: ruling 1 keeps
    unrevealed names and image answers out of the projection's scope
    entirely, so a frame layer that had lost its `revealed` check would
    still have nothing to put in a frame. Over-answering separates the two
    layers, so ruling 1's tests assert what was *asked* and §11's assert
    what the frame does with content it did not ask for.
    """
    names, answers = content_of(state)
    return OverAnsweringContentDirectory(names, answers)


def dumped(frame: Any) -> dict[str, Any]:
    return dict(frame.model_dump(mode="json"))


async def test_no_answer_string_appears_anywhere_in_a_stage_frame() -> None:
    """§11's «Проекции» row, first half.

    Kills on: any future field carrying an image answer into the stage
    frame — a `current_answer` copied across from the host projection, a
    debug field, a whole `ContentDescription` attached "for the client's
    convenience"."""
    state, *_ = build_duel_state()
    # Over-answering on purpose — see the helper's own docstring. Against an
    # honest directory this assertion cannot fail, because ruling 1 keeps
    # every answer out of the projection's scope one layer earlier.
    directory = over_answering_directory_for(state)

    frame = await project_stage(state, now=NOW, events=(), directory=directory)

    strings = every_string(dumped(frame))
    assert not [s for s in strings if ANSWER_PREFIX in s]


async def test_no_unrevealed_category_name_appears_anywhere_in_a_stage_frame() -> None:
    """§11's «Проекции» row, second half.

    Kills on: projecting `group.category` through the host's view, which is
    the one-line change that would put every secret on the big screen."""
    state, *_ = build_duel_state()
    directory = over_answering_directory_for(state)
    hidden_names = {
        f"{NAME_PREFIX}{index}"
        for index, group in enumerate(state.groups.values())
        if not group.revealed
    }
    assert hidden_names, "the fixture must contain unrevealed groups for this to test anything"

    frame = await project_stage(state, now=NOW, events=(), directory=directory)

    strings = set(every_string(dumped(frame)))
    assert not (strings & hidden_names)


async def test_the_stage_projection_never_asks_for_an_unrevealed_category() -> None:
    """Ruling 1, and strictly stronger than the two walks above: there is
    nothing in the projection's own scope to leak.

    Kills on: collecting every group's category and filtering on the way
    out — a projection that would still pass the whole-tree test today and
    leak the first time somebody widened the frame."""
    state, *_ = build_duel_state()
    directory = directory_for(state)
    unrevealed = frozenset(
        group.category for group in state.groups.values() if not group.revealed
    )

    await project_stage(state, now=NOW, events=(), directory=directory)

    assert not (directory.asked_categories & unrevealed)


async def test_the_stage_projection_never_asks_for_an_image() -> None:
    """Ruling 1: `images=frozenset()`, always.

    Kills on: asking for the current image "so the frame can carry a
    thumbnail id", which would put every answer in the projection's scope."""
    state, *_ = build_duel_state()
    directory = directory_for(state)

    await project_stage(state, now=NOW, events=(), directory=directory)

    assert directory.asked_images == frozenset()
    assert all(request.images == frozenset() for request in directory.requests)


async def test_a_revealed_group_is_named_and_an_unrevealed_one_is_hidden() -> None:
    state, *_ = build_duel_state()
    directory = directory_for(state)

    frame = await project_stage(state, now=NOW, events=(), directory=directory)

    by_id = {group.id: group for group in frame.groups}
    for group in state.groups.values():
        projected = by_id[group.id].category
        assert isinstance(projected, NamedCategory if group.revealed else HiddenCategory)


async def test_a_revealed_group_the_library_cannot_name_is_hidden() -> None:
    """Ruling 3: `hidden` covers «имени нет» too, so a content defect reads
    as a secret on the big screen — visible, not silent.

    Kills on: `NamedCategory(name=None)` or an empty-string name, either of
    which would render as a blank label the room cannot interpret."""
    state, *_ = build_duel_state()
    directory = RecordingContentDirectory({}, {})

    frame = await project_stage(state, now=NOW, events=(), directory=directory)

    assert all(isinstance(group.category, HiddenCategory) for group in frame.groups)


async def test_the_host_sees_the_current_answer_and_the_unrevealed_names() -> None:
    """§7.1: «Пульт получает всё.»"""
    state, *_ = build_duel_state()
    directory = directory_for(state)
    assert state.duel is not None

    frame = await project_host(state, now=NOW, events=(), directory=directory)

    assert frame.duel is not None
    assert frame.duel.current_answer == f"{ANSWER_PREFIX}{state.duel.index}"
    assert all(group.category.name is not None for group in frame.groups)


async def test_the_host_is_told_about_one_image_and_not_the_whole_pack() -> None:
    """Kills on: asking for every image in the order. Sixty answers on the
    wire per judgement, and fifty-nine of them the operator will not read
    before the pack is redrawn."""
    state, *_ = build_duel_state()
    directory = directory_for(state)

    await project_host(state, now=NOW, events=(), directory=directory)

    assert len(directory.asked_images) == 1


async def test_the_host_sees_a_nameless_category_as_null() -> None:
    """Ruling 3's other half: the defect is in front of the operator."""
    state, *_ = build_duel_state()

    frame = await project_host(state, now=NOW, events=(), directory=RecordingContentDirectory())

    assert all(group.category.name is None for group in frame.groups)
    assert all(group.category.id is not None for group in frame.groups)


async def test_legal_attacks_lists_only_adjacent_enemy_groups_of_the_current_player() -> None:
    """§9.2: «Правило смежности не проверяется, а делается невозможным.»

    Kills on: listing every enemy group, which would put an attack in front
    of the operator that `decide` then refuses with NOT_ADJACENT."""
    state, _players = build_running_state()
    directory = directory_for(state)

    frame = await project_host(state, now=NOW, events=(), directory=directory)

    current = state.current_player()
    assert set(frame.legal_attacks) == {
        group.id for group in state.groups.values() if group.owner == current
    }
    for raw_id, targets in frame.legal_attacks.items():
        # The frame's keys are plain UUIDs — `GroupId` is a `NewType`, and
        # Pydantic keeps the runtime value while dropping the alias.
        group_id = GroupId(raw_id)
        assert set(targets) == set(legal_targets(state, group_id))
        assert all(state.groups[GroupId(target)].owner != current for target in targets)


async def test_no_attack_is_legal_while_a_duel_is_running() -> None:
    """Kills on: computing targets regardless of the duel, which offers the
    console a button `decide` refuses with DUEL_IN_PROGRESS."""
    state, *_ = build_duel_state()

    frame = await project_host(state, now=NOW, events=(), directory=directory_for(state))

    assert frame.legal_attacks == {}


async def test_both_frames_carry_the_batch_s_wire_names() -> None:
    """Ruling 13. Kills on: `type(event).__name__`, which would make a
    class rename a wire change — exactly what plan 2's frozen registry
    exists to prevent."""
    state, *_ = build_duel_state()
    directory = directory_for(state)
    events = decide(state, PauseDuel(), DecisionContext(now=BASE_TIME))

    stage = await project_stage(state, now=NOW, events=events, directory=directory)
    host = await project_host(state, now=NOW, events=events, directory=directory)

    assert stage.last_event_types == ("duel.paused",)
    assert host.last_event_types == ("duel.paused",)


async def test_a_resolved_duel_puts_its_capture_in_the_frame() -> None:
    """Ruling 13: §9.1's third beat cannot be reconstructed by diffing
    against a frame the client may never have received (§7.2).

    Kills on: dropping `resolution`, which turns the capture animation into
    a merge the stage can see happened but cannot describe."""
    state, *_ = build_duel_state()
    assert state.duel is not None
    # Pass every image in the pack: the last one resolves the duel.
    events: tuple[Event, ...] = ()
    while state.duel is not None:
        events = decide(state, JudgePass(), DecisionContext(now=BASE_TIME))
        state = fold(state, events)

    frame = await project_stage(state, now=NOW, events=events, directory=directory_for(state))

    assert frame.resolution is not None
    assert frame.resolution.absorbed_cells
    assert frame.duel is None


async def test_a_frame_carries_the_deadline_and_the_server_s_own_now() -> None:
    """§7.3: the client corrects for clock skew against `server_now` and
    interpolates itself — the server sends no ticks.

    Kills on: dropping `server_now`, which leaves a browser whose clock is
    minutes off drawing a timer that is minutes wrong."""
    state, *_ = build_duel_state()

    frame = await project_stage(state, now=NOW, events=(), directory=directory_for(state))

    assert frame.server_now == NOW
    assert frame.duel is not None
    assert state.duel is not None
    assert frame.duel.timing.deadline_at == deadline_of(state.duel)
    assert frame.duel.timing.deadline_at is not None


async def test_a_paused_duel_has_no_deadline() -> None:
    """Kills on: computing a deadline from a null anchor — the room would
    watch a timer run out while nothing was happening."""
    state, *_ = build_duel_state()
    state = apply(state, PauseDuel(), now=BASE_TIME)

    frame = await project_stage(state, now=NOW, events=(), directory=directory_for(state))

    assert frame.duel is not None
    assert frame.duel.timing.paused
    assert frame.duel.timing.deadline_at is None
    assert frame.duel.timing.anchor is None


async def test_timing_carries_both_players_remainders() -> None:
    """§7.3 names both. Kills on: projecting only `answering`'s budget."""
    state, *_ = build_duel_state()

    frame = await project_stage(state, now=NOW, events=(), directory=directory_for(state))

    assert frame.duel is not None
    assert state.duel is not None
    assert set(frame.duel.timing.remaining_ms) == set(state.duel.budgets.players())
    assert len(frame.duel.timing.remaining_ms) == 2


async def test_a_declared_duel_carries_the_whole_image_pack_to_the_stage() -> None:
    """§3.5 and §9.1: the pack is drawn at declaration so the screen can
    preload it while the operator explains the category.

    Kills on: sending only the current image id, which makes the warm-up
    window fictional — the preload list and the live deadline would arrive
    in one frame."""
    state, *_ = build_declared_state()

    frame = await project_stage(state, now=NOW, events=(), directory=directory_for(state))

    assert state.duel is not None
    assert frame.duel is not None
    assert frame.duel.image_order == tuple(state.duel.image_order)
    assert len(frame.duel.image_order) > 1


async def test_a_match_in_setup_has_no_current_player() -> None:
    """Kills on: calling `current_player()` unconditionally, which indexes
    an empty `turn_order` and turns every pre-start frame into an
    IndexError inside a writer task."""
    state, _players = build_dealt_state()

    frame = await project_stage(state, now=NOW, events=(), directory=directory_for(state))
    host = await project_host(state, now=NOW, events=(), directory=directory_for(state))

    assert frame.current_player is None
    assert host.current_player is None
    assert host.legal_attacks == {}


@pytest.mark.parametrize("unknown", [CategoryId(uuid4())])
async def test_a_directory_answering_about_ids_it_was_not_asked_changes_nothing(
    unknown: CategoryId,
) -> None:
    """Kills on: keying the projection off the description's contents
    rather than off the state's own groups — a directory returning extra
    ids would then add phantom groups to the frame."""
    state, *_ = build_running_state()
    directory = RecordingContentDirectory({unknown: "ЧУЖОЕ"}, {})

    frame = await project_stage(state, now=NOW, events=(), directory=directory)

    assert len(frame.groups) == len(state.groups)
    assert "ЧУЖОЕ" not in every_string(dumped(frame))
