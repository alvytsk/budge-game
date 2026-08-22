from podvinsya.domain.state import MatchState


def next_turn(state: MatchState) -> tuple[int, int]:
    """Return (turn_index, round_no) for the next living player.

    Eliminated players are skipped. The round number increases whenever the
    cursor wraps past the end of the fixed turn order.
    """
    size = len(state.turn_order)
    index = state.turn_index
    round_no = state.round_no
    for _ in range(size):
        index += 1
        if index >= size:
            index = 0
            round_no += 1
        if not state.player(state.turn_order[index]).eliminated:
            return index, round_no
    return state.turn_index, state.round_no
