"""Logging one operator in, and out.

§7.5: «Ведущий логинится: один оператор, пароль, сессионная кука.» There is
no user table, no registration and no password reset — rotating
`BUDGE_HOST_PASSWORD` with `budge hash-password` is all of those.
"""

from fastapi import APIRouter, Request, Response, status

from budge.api.principal import SESSION_COOKIE
from budge.api.schemas.rest import LoginBody
from budge.api.security import mint_session, verify_password
from budge.api.settings import ApiSettings
from budge.services.ports import Clock

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def log_in(body: LoginBody, request: Request, response: Response) -> Response:
    """Verify first, set the cookie second.

    The failure carries no detail about which half was wrong, because there
    is only one half — but a message naming "unknown password" would still
    confirm to a caller that they had reached the right service.
    """
    settings: ApiSettings = request.app.state.settings
    clock: Clock = request.app.state.clock
    if not verify_password(body.password, settings.host_password):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    response.set_cookie(
        SESSION_COOKIE,
        mint_session(settings.secret_key, issued_at=clock.now()),
        # HttpOnly: §9.3 puts both surfaces in one Vite application, so
        # anything that ends up in that bundle runs on the same origin as
        # the console. A readable session cookie would be one XSS away from
        # an operator's whole match.
        httponly=True,
        # Lax rather than Strict: the stage screen is opened by following a
        # link, and Strict would break a bookmarked console the same way.
        samesite="lax",
        # No `secure`: §1.1 puts this on an isolated network, and §10 puts
        # Caddy in front — so the flag is either redundant (Caddy
        # terminates TLS) or fatal (a plain-http meeting-room deployment,
        # where a `secure` cookie is simply never sent back and the
        # operator cannot log in at all).
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def log_out(response: Response) -> Response:
    """Deliberately unauthenticated.

    Logging out of a session that has already expired must work — otherwise
    the one state an operator cannot leave is the one they most want to.
    """
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
