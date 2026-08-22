from podvinsya.domain.board import BoardSize
from podvinsya.domain.ids import MatchId
from podvinsya.domain.settings import MatchSettings
from podvinsya.domain.state import MatchState, MatchStatus


def create_initial_state(
    match_id: MatchId, board: BoardSize, settings: MatchSettings
) -> MatchState:
    """The genesis constructor. Recovery is fold(evolve, create_initial_state(), events).

    This lives in production code on purpose: a genesis constructor that exists
    only as a test fixture means recovery is not actually "fold the log".
    """
    return MatchState(
        id=match_id,
        seq=0,
        status=MatchStatus.SETUP,
        board=board,
        settings=settings,
    )
