"""The three primitives §7.5 needs, and the properties that make them safe.

No HTTP here: these are pure functions over strings and datetimes, so the
tests are pure too, and every fixed instant below is written out rather
than read from a clock.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from budge.api.security import (
    hash_password,
    mint_session,
    mint_stage_token,
    read_session,
    read_stage_token,
    verify_password,
)
from budge.domain.ids import MatchId

SECRET = "a-secret-that-is-only-a-test-secret"
NOON = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=12)


def test_a_password_verifies_against_its_own_hash() -> None:
    assert verify_password("hunter2", hash_password("hunter2"))


def test_a_wrong_password_does_not_verify() -> None:
    assert not verify_password("hunter3", hash_password("hunter2"))


def test_two_hashes_of_one_password_differ() -> None:
    """Salting, stated as a property. Without a per-hash random salt, two
    deployments choosing the same password would carry the same string, and
    a hash leaked from one would be recognisable in the other."""
    assert hash_password("hunter2") != hash_password("hunter2")


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "not-even-close",
        "scrypt$16384$8$1$onlyfourfields",
        "bcrypt$16384$8$1$c2FsdA$a2V5",
        "scrypt$notanumber$8$1$c2FsdA$a2V5",
    ],
)
def test_a_malformed_hash_verifies_nothing(encoded: str) -> None:
    """Kills on: letting the ValueError out of `verify_password`. A
    misconfigured BUDGE_HOST_PASSWORD must refuse every login, not turn
    the login endpoint into a 500 that reveals the parse failure."""
    assert not verify_password("hunter2", encoded)


def test_a_fresh_session_reads_back() -> None:
    assert read_session(SECRET, mint_session(SECRET, issued_at=NOON), now=NOON, ttl=TTL)


def test_a_session_past_its_ttl_is_refused() -> None:
    token = mint_session(SECRET, issued_at=NOON)
    assert not read_session(SECRET, token, now=NOON + TTL + timedelta(seconds=1), ttl=TTL)


def test_a_session_from_the_future_is_refused() -> None:
    """One minute of forward skew is allowed and no more. A cookie stamped
    hours ahead is either a forgery or a clock so wrong that nothing else
    in this system works either."""
    token = mint_session(SECRET, issued_at=NOON + timedelta(hours=2))
    assert not read_session(SECRET, token, now=NOON, ttl=TTL)


def test_a_session_signed_with_another_key_is_refused() -> None:
    token = mint_session("some-other-key", issued_at=NOON)
    assert not read_session(SECRET, token, now=NOON, ttl=TTL)


def test_a_tampered_session_payload_is_refused() -> None:
    """Kills on: comparing only the payload, or trusting it before the MAC.
    The timestamp is moved forward while the signature stays; a verifier
    that parsed first and checked second would accept it."""
    token = mint_session(SECRET, issued_at=NOON - timedelta(days=30))
    payload, _, signature = token.rpartition(".")
    subject, _, _issued = payload.partition(".")
    forged = f"{subject}.{int(NOON.timestamp())}.{signature}"
    assert not read_session(SECRET, forged, now=NOON, ttl=TTL)


def test_a_stage_token_names_its_own_match() -> None:
    match_id = MatchId(uuid4())
    assert read_stage_token(SECRET, mint_stage_token(SECRET, match_id)) == match_id


def test_a_stage_token_for_one_match_does_not_open_another() -> None:
    """The token is bound to the match, so last week's link cannot watch
    tonight's game. Kills on: signing a constant instead of the match id."""
    first, second = MatchId(uuid4()), MatchId(uuid4())
    assert read_stage_token(SECRET, mint_stage_token(SECRET, first)) != second


def test_a_stage_token_with_a_swapped_match_id_is_refused() -> None:
    token = mint_stage_token(SECRET, MatchId(uuid4()))
    _, _, signature = token.rpartition(".")
    assert read_stage_token(SECRET, f"stage.{uuid4()}.{signature}") is None


def test_a_stage_token_whose_payload_is_not_a_uuid_is_refused() -> None:
    """Kills on: dropping the UUID parse guard. A payload that passes the
    MAC can still fail to be a match id — every signed string this server
    ever mints shares one key — and a ValueError escaping here would turn
    the stage endpoint into a 500 instead of a refusal."""
    token = mint_session(SECRET, issued_at=NOON)
    _, _, signature = token.rpartition(".")
    assert read_stage_token(SECRET, f"stage.not-a-uuid.{signature}") is None


def test_a_session_cookie_is_not_a_stage_token() -> None:
    """Kills on: signing both with the same payload shape. Without the
    subject prefix, a host cookie would be a valid stage token and the
    reverse, and §7.5's two-projection decision would be forgeable."""
    assert read_stage_token(SECRET, mint_session(SECRET, issued_at=NOON)) is None
    assert not read_session(SECRET, mint_stage_token(SECRET, MatchId(uuid4())), now=NOON, ttl=TTL)


def test_an_unsigned_string_is_neither() -> None:
    """Kills on: `_unsign` returning the payload when the token carries no
    separator at all, which would make every bare string a valid token."""
    assert read_stage_token(SECRET, "stage") is None
    assert not read_session(SECRET, "host", now=NOON, ttl=TTL)


def test_a_stage_token_round_trips_to_a_uuid() -> None:
    """The return type is a `MatchId`, which is a `UUID` at runtime — a
    caller comparing it against a database column must not get a string."""
    match_id = MatchId(uuid4())
    assert isinstance(read_stage_token(SECRET, mint_stage_token(SECRET, match_id)), UUID)
