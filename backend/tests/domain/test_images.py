from dataclasses import replace

import pytest

from budge.domain.actions import JudgeCorrect, JudgePass
from budge.domain.errors import Rejected, RejectionReason
from budge.domain.state import MatchState

from .conftest import apply, at, build_duel_state


def _shorten(state: MatchState, count: int) -> MatchState:
    """Trim the pack drawn at declaration time down to `count` images."""
    duel = state.duel
    assert duel is not None
    return replace(state, duel=replace(duel, image_order=duel.image_order[:count]))


def test_a_correct_answer_past_the_last_image_is_rejected() -> None:
    """A duel is not bounded by the image count: a correct answer costs no budget."""
    state, _, _, _ = build_duel_state()
    state = _shorten(state, 2)

    state = apply(state, JudgeCorrect(), now=at(1))
    duel = state.duel
    assert duel is not None
    assert duel.index == 1

    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgeCorrect(), now=at(2))
    assert excinfo.value.reason is RejectionReason.IMAGES_EXHAUSTED


def test_a_pass_past_the_last_image_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    state = _shorten(state, 1)

    with pytest.raises(Rejected) as excinfo:
        apply(state, JudgePass(), now=at(1))
    assert excinfo.value.reason is RejectionReason.IMAGES_EXHAUSTED


def test_exhaustion_refuses_without_resolving_the_duel() -> None:
    """Spec 8: running out of images is a content defect, not a game transition."""
    state, _, _, _ = build_duel_state()
    state = _shorten(state, 1)
    before = state.duel
    assert before is not None

    with pytest.raises(Rejected):
        apply(state, JudgeCorrect(), now=at(2))

    assert state.duel == before, "a refusal must leave the state untouched"
    assert state.winner is None


def test_the_index_never_leaves_the_pack() -> None:
    """The stage screen reads image_order[index] live; walking off the end raises."""
    state, _, _, _ = build_duel_state()
    state = _shorten(state, 3)
    for second in (1, 2):
        state = apply(state, JudgeCorrect(), now=at(second))
        duel = state.duel
        assert duel is not None
        assert duel.image_order[duel.index] is not None
    with pytest.raises(Rejected):
        apply(state, JudgePass(), now=at(3))
