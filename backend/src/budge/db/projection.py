"""The read model of §5.2, maintained in the appending transaction.

It carries what an admin list needs and nothing else. It is not
authoritative: `rebuild` replays the log over it, and the test that matters
is that both paths agree.

The projection reads *events*, not folded state, because §6.2 folds only
after the transaction has committed — inside the transaction, events are all
there is. That constraint is what lets `apply_events` serve both the
incremental path and the rebuild.
"""

from collections.abc import Iterable

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from budge.db.models import Match, MatchPlayer
from budge.domain.events import (
    Event,
    MatchReset,
    MatchStarted,
    MatchWon,
    PlayerAdded,
    PlayerEliminated,
)
from budge.domain.ids import MatchId
from budge.domain.state import MatchStatus


async def apply_events(
    session: AsyncSession, match_id: MatchId, events: Iterable[Event]
) -> None:
    """Fold the read model forward over one batch. Events it does not
    recognize change nothing — most of the log is duel detail the admin list
    has no opinion about."""
    for event in events:
        match event:
            case PlayerAdded():
                session.add(
                    MatchPlayer(
                        match_id=match_id,
                        player_id=event.player_id,
                        name=event.name,
                        colour=event.colour,
                        eliminated=False,
                    )
                )
            case MatchStarted():
                await session.execute(
                    update(Match)
                    .where(Match.id == match_id)
                    .values(status=MatchStatus.RUNNING.value)
                )
            case MatchReset():
                # §A.6: line for line the first half of `rebuild`. The
                # overlap is not a coincidence -- it is what makes the
                # "incremental path and rebuild agree" test cover a reset
                # too, for free.
                if event.keep_roster:
                    await session.execute(
                        update(MatchPlayer)
                        .where(MatchPlayer.match_id == match_id)
                        .values(eliminated=False)
                    )
                else:
                    await session.execute(
                        delete(MatchPlayer).where(MatchPlayer.match_id == match_id)
                    )
                await session.execute(
                    update(Match)
                    .where(Match.id == match_id)
                    .values(status=MatchStatus.SETUP.value, winner_id=None)
                )
            case PlayerEliminated():
                await session.execute(
                    update(MatchPlayer)
                    .where(
                        MatchPlayer.match_id == match_id,
                        MatchPlayer.player_id == event.player_id,
                    )
                    .values(eliminated=True)
                )
            case MatchWon():
                await session.execute(
                    update(Match)
                    .where(Match.id == match_id)
                    .values(status=MatchStatus.FINISHED.value, winner_id=event.player_id)
                )
            case _:
                pass
    await session.flush()


async def rebuild(session: AsyncSession, match_id: MatchId, events: Iterable[Event]) -> None:
    """Throw the read model away and replay the log over it."""
    await session.execute(delete(MatchPlayer).where(MatchPlayer.match_id == match_id))
    await session.execute(
        update(Match)
        .where(Match.id == match_id)
        .values(status=MatchStatus.SETUP.value, winner_id=None)
    )
    await apply_events(session, match_id, events)
