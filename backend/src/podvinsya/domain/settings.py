from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MatchSettings:
    base_seconds: int = 60
    bonus_cap_seconds: int = 15
    pass_penalty_seconds: int = 3

    @property
    def base_ms(self) -> int:
        return self.base_seconds * 1000

    @property
    def bonus_cap_ms(self) -> int:
        return self.bonus_cap_seconds * 1000

    @property
    def pass_penalty_ms(self) -> int:
        return self.pass_penalty_seconds * 1000
