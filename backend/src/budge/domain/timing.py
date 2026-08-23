from datetime import datetime, timedelta

from budge.domain.state import Duel


def elapsed_ms(anchor: datetime | None, now: datetime, remaining_ms: int) -> int:
    """Time to charge the answering player, clamped into [0, remaining_ms].

    The lower bound guards against a clock stepping backwards: without it a
    negative difference would *give* the player time. The upper bound is its
    pair and never fires silently — reaching it means the timer already
    expired, and decide() resolves the duel instead of applying the command.
    """
    if anchor is None:
        return 0
    delta = int((now - anchor).total_seconds() * 1000)
    return max(0, min(delta, remaining_ms))


def deadline_of(duel: Duel) -> datetime | None:
    if duel.anchor is None:
        return None
    return duel.anchor + timedelta(milliseconds=duel.budgets.get(duel.answering))


def is_expired(duel: Duel, now: datetime) -> bool:
    deadline = deadline_of(duel)
    return deadline is not None and now >= deadline
