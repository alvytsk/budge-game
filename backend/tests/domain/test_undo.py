import pytest

from podvinsya.domain.actions import JudgeCorrect, JudgePass, UndoLastJudgement
from podvinsya.domain.context import JournalEntry
from podvinsya.domain.errors import Rejected, RejectionReason

from .conftest import apply, at, build_duel_state


def _snapshot(state, seq: int) -> JournalEntry:  # type: ignore[no-untyped-def]
    duel = state.duel
    assert duel is not None
    return JournalEntry(
        seq=seq,
        budgets=duel.budgets,
        answering=duel.answering,
        image_index=duel.index,
    )


def test_undo_restores_budgets_answerer_and_image() -> None:
    state, _, _, _ = build_duel_state()
    snapshot = _snapshot(state, seq=state.seq)
    before = state.duel
    assert before is not None

    state = apply(state, JudgeCorrect(), now=at(4.2))
    state = apply(state, UndoLastJudgement(), now=at(6), duel_journal=(snapshot,))

    duel = state.duel
    assert duel is not None
    assert duel.answering == before.answering
    assert duel.index == before.index
    assert duel.budgets == before.budgets


def test_time_between_the_mistake_and_the_undo_is_not_charged() -> None:
    state, _, _, _ = build_duel_state()
    snapshot = _snapshot(state, seq=state.seq)
    before = state.duel
    assert before is not None
    start = before.budgets.get(before.answering)

    state = apply(state, JudgeCorrect(), now=at(4.2))
    state = apply(state, UndoLastJudgement(), now=at(30), duel_journal=(snapshot,))

    duel = state.duel
    assert duel is not None
    assert duel.budgets.get(before.answering) == start, (
        "the operator fixing their own mistake must not cost the player"
    )


def test_undo_reanchors_so_the_clock_restarts_from_now() -> None:
    state, _, _, _ = build_duel_state()
    snapshot = _snapshot(state, seq=state.seq)
    state = apply(state, JudgeCorrect(), now=at(4.2))
    state = apply(state, UndoLastJudgement(), now=at(30), duel_journal=(snapshot,))
    duel = state.duel
    assert duel is not None
    assert duel.anchor == at(30)


def test_undo_walks_back_a_chain() -> None:
    state, _, _, _ = build_duel_state()
    first = _snapshot(state, seq=state.seq)
    original = state.duel
    assert original is not None

    state = apply(state, JudgeCorrect(), now=at(3))
    second = _snapshot(state, seq=state.seq)
    state = apply(state, JudgePass(), now=at(6))

    state = apply(state, UndoLastJudgement(), now=at(7), duel_journal=(first, second))
    duel = state.duel
    assert duel is not None
    assert duel.index == 1

    state = apply(state, UndoLastJudgement(), now=at(8), duel_journal=(first,))
    duel = state.duel
    assert duel is not None
    assert duel.index == 0
    assert duel.budgets == original.budgets


def test_undo_with_nothing_to_undo_is_rejected() -> None:
    state, _, _, _ = build_duel_state()
    with pytest.raises(Rejected) as excinfo:
        apply(state, UndoLastJudgement(), now=at(3), duel_journal=())
    assert excinfo.value.reason is RejectionReason.NOTHING_TO_UNDO


def test_undo_without_a_duel_is_rejected() -> None:
    from .conftest import build_running_state

    state, _ = build_running_state(4)
    with pytest.raises(Rejected) as excinfo:
        apply(state, UndoLastJudgement(), now=at(3))
    assert excinfo.value.reason is RejectionReason.NO_DUEL
