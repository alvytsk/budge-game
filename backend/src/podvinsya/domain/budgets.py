from collections.abc import Mapping
from dataclasses import dataclass

from podvinsya.domain.ids import PlayerId


@dataclass(frozen=True, slots=True)
class Budgets:
    """Remaining duel time per player, as an immutable value.

    Stored as ordered pairs rather than a mapping so the whole value stays
    hashable and serialises to the event log without ambiguity.
    """

    entries: tuple[tuple[PlayerId, int], ...]

    @classmethod
    def of(cls, values: Mapping[PlayerId, int]) -> "Budgets":
        return cls(tuple(values.items()))

    def get(self, player: PlayerId) -> int:
        for owner, remaining in self.entries:
            if owner == player:
                return remaining
        raise KeyError(player)

    def players(self) -> tuple[PlayerId, ...]:
        return tuple(owner for owner, _ in self.entries)

    def with_value(self, player: PlayerId, remaining_ms: int) -> "Budgets":
        clamped = max(0, remaining_ms)
        return Budgets(
            tuple(
                (owner, clamped if owner == player else value)
                for owner, value in self.entries
            )
        )

    def charge(self, player: PlayerId, amount_ms: int) -> "Budgets":
        return self.with_value(player, self.get(player) - amount_ms)
