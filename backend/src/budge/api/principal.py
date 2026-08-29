"""Who is calling, derived from the transport and from nothing else.

§7.4: «Клиент **никогда не передаёт, кто он**. Принципал выводится из
аутентифицированной сессии.» So every function here reads a cookie or a
path segment, and none of them reads a body. There is no code path from a
payload to a principal, which is what makes the check «названный актор
действительно участник» unnecessary rather than merely present.
"""

from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request, status
from starlette.requests import HTTPConnection

from budge.api.security import read_session, read_stage_token
from budge.api.settings import ApiSettings
from budge.domain.ids import MatchId
from budge.services.ports import Clock

SESSION_COOKIE = "budge_session"


@dataclass(frozen=True, slots=True)
class HostPrincipal:
    """The operator. Deliberately empty.

    §1.1 and §7.5 give this deployment exactly one operator, and a
    principal carrying a name would be a second source of identity next to
    the cookie — one that a later route could start comparing a payload
    against, which is the shape §7.4 exists to forbid.
    """


@dataclass(frozen=True, slots=True)
class StagePrincipal:
    """A screen, and the one match its link named (ruling 7)."""

    match_id: MatchId


def host_from_cookie(connection: HTTPConnection) -> HostPrincipal | None:
    """The operator's principal, or `None`. Never raises.

    It takes an `HTTPConnection` — the base both `Request` and `WebSocket`
    derive from — because the cookie is read the same way over both, and
    the WebSocket routes need the "or None" form: they must decide whether
    to accept the handshake at all, and an `HTTPException` raised inside a
    WebSocket scope produces a protocol error rather than a clean close.
    """
    settings: ApiSettings = connection.app.state.settings
    clock: Clock = connection.app.state.clock
    token = connection.cookies.get(SESSION_COOKIE)
    if token is None:
        return None
    valid = read_session(
        settings.secret_key,
        token,
        now=clock.now(),
        ttl=timedelta(hours=settings.session_ttl_hours),
    )
    return HostPrincipal() if valid else None


def require_host(request: Request) -> HostPrincipal:
    """The FastAPI dependency every REST route under `/api/matches` takes.

    401, not 403: there is one operator and one password, so "we do not
    know who you are" is always the true answer — a 403 would imply the
    server had identified somebody and found them insufficient.
    """
    principal = host_from_cookie(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a valid session cookie is required",
        )
    return principal


def stage_principal_for(settings: ApiSettings, token: str) -> StagePrincipal | None:
    """Ruling 7: the token *is* the match. No column, no lookup, and a link
    to yesterday's match cannot watch today's."""
    match_id = read_stage_token(settings.secret_key, token)
    return StagePrincipal(match_id=match_id) if match_id is not None else None
