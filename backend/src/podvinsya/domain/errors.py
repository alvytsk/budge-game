from enum import StrEnum


class RejectionReason(StrEnum):
    BOARD_INVALID = "board_invalid"
    BOARD_NOT_DIVISIBLE = "board_not_divisible"
    WRONG_STATUS = "wrong_status"
    PLAYER_COUNT_INVALID = "player_count_invalid"
    DUPLICATE_PLAYER = "duplicate_player"
    UNKNOWN_PLAYER = "unknown_player"
    SECRET_MISSING = "secret_missing"
    DUPLICATE_CATEGORY = "duplicate_category"
    DEAL_INVALID = "deal_invalid"
    NOT_YOUR_TURN = "not_your_turn"
    UNKNOWN_GROUP = "unknown_group"
    NOT_YOUR_GROUP = "not_your_group"
    TARGET_IS_YOURS = "target_is_yours"
    NOT_ADJACENT = "not_adjacent"
    DUEL_IN_PROGRESS = "duel_in_progress"
    NO_DUEL = "no_duel"
    DUEL_NOT_DECLARED = "duel_not_declared"
    DUEL_NOT_RUNNING = "duel_not_running"
    DUEL_PAUSED = "duel_paused"
    DUEL_NOT_PAUSED = "duel_not_paused"
    NOTHING_TO_UNDO = "nothing_to_undo"
    IMAGES_EXHAUSTED = "images_exhausted"


class Rejected(Exception):
    """A legal-but-refused command. Never a fault: state stays untouched."""

    def __init__(self, reason: RejectionReason) -> None:
        super().__init__(reason.value)
        self.reason = reason
