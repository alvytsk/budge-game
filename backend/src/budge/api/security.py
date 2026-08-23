"""Password hashing and signed tokens, on the standard library alone.

§7.5 asks for one operator, one password, a session cookie, and a per-match
link for the screen. That is a small enough surface that `hashlib.scrypt`
and `hmac` cover it exactly, and adding a KDF library would put an upgrade
cadence on a machine that lives in a meeting room (§1.1).

Every comparison here goes through `hmac.compare_digest`. Timing is a weak
channel on an isolated network, but the constant-time call is one character
longer than the variable-time one, so there is no trade to make.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from budge.domain.ids import MatchId

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_HOST_SUBJECT = "host"
_STAGE_SUBJECT = "stage"

# A cookie stamped slightly ahead of the server is a clock skew; one stamped
# far ahead is a forgery or a broken deployment. Neither should be honoured
# beyond the width of ordinary NTP drift.
_FORWARD_SKEW = timedelta(minutes=1)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """Encode as `scrypt$n$r$p$salt$key`, all base64url, no padding.

    The parameters travel with the hash so raising them later does not
    invalidate hashes produced before the change.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_BYTES
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    """False for a wrong password and false for a malformed hash.

    A malformed hash is a misconfiguration, and the right behaviour is to
    refuse every login — not to raise out of the login endpoint, where the
    parse failure would reach the client as a 500 that says more about the
    deployment than an attacker should learn.
    """
    try:
        scheme, n, r, p, salt, key = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(key)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def _sign(secret: str, payload: str) -> str:
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}"


def _unsign(secret: str, token: str) -> str | None:
    payload, dot, signature = token.rpartition(".")
    if not dot:
        return None
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    try:
        given = _unb64(signature)
    except (ValueError, binascii.Error):
        return None
    return payload if hmac.compare_digest(expected, given) else None


def mint_session(secret: str, *, issued_at: datetime) -> str:
    return _sign(secret, f"{_HOST_SUBJECT}.{int(issued_at.timestamp())}")


def read_session(secret: str, token: str, *, now: datetime, ttl: timedelta) -> bool:
    """True only for an unexpired cookie this server signed for the host.

    The subject prefix is what keeps a stage token from reading as a session
    and the reverse: both are signed with the same key, so without it the
    two would be interchangeable and §7.5's whole distinction — which of the
    two projections to build — would be something a client could choose.
    """
    payload = _unsign(secret, token)
    if payload is None:
        return False
    subject, dot, issued = payload.partition(".")
    if not dot or subject != _HOST_SUBJECT:
        return False
    try:
        issued_at = datetime.fromtimestamp(int(issued), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return now - ttl <= issued_at <= now + _FORWARD_SKEW


def mint_stage_token(secret: str, match_id: MatchId) -> str:
    """A derived token, not a stored one (see the plan's ruling 7): no
    column, no lookup, and bound to exactly one match."""
    return _sign(secret, f"{_STAGE_SUBJECT}.{match_id}")


def read_stage_token(secret: str, token: str) -> MatchId | None:
    payload = _unsign(secret, token)
    if payload is None:
        return None
    subject, dot, raw_id = payload.partition(".")
    if not dot or subject != _STAGE_SUBJECT:
        return None
    try:
        return MatchId(UUID(raw_id))
    except ValueError:
        return None
