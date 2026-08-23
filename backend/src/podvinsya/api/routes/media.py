"""Take a picture in; hand it back by its address.

Two routes and one asymmetry worth stating plainly: uploading requires the
operator, and fetching requires nobody (ruling 5).

Every other route in this system takes a principal, so the exception is
written down rather than left to be noticed. The stage screen holds a token
in its URL path and no cookie (§7.5), and an `<img src>` cannot carry a
bearer header — so requiring a principal here would mean threading a token
through every image URL on the one screen whose whole job is rendering
pictures. What guards the endpoint instead is the address: a sha256 is 256
bits, unguessable, and only ever learned from a frame the server chose to
send. §1.1 puts the deployment on an isolated network, and §7.5 says the
authentication there exists «чтобы сервер знал, какую из двух проекций
строить», not to withstand an adversary.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from podvinsya.api.principal import require_host
from podvinsya.api.schemas.media import UploadedMediaBody
from podvinsya.api.settings import ApiSettings
from podvinsya.media.digest import SNIFFED_TYPES, sniff
from podvinsya.services.ports import MediaStore, MediaUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])

_DIGEST = re.compile(r"^[0-9a-f]{64}$")

# A content address names one immutable sequence of bytes, so this is not a
# heuristic about how long the answer stays fresh — it is a statement of
# fact about this URL (ruling 8). §9.1 preloads a whole pack at
# declaration, and a second duel on the same category, or a screen that
# reconnects mid-show, must not fetch it again.
_IMMUTABLE = "public, max-age=31536000, immutable"


def _store(request: Request) -> MediaStore:
    store: MediaStore = request.app.state.media
    return store


def _settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_host)])
async def upload(request: Request) -> UploadedMediaBody:
    """The body is bytes and nothing else.

    No filename, no declared content type, no digest (§7.4 and ruling 2):
    each of those would be something the client said about itself, and each
    is either derivable from the bytes or irrelevant. The digest in
    particular is computed here — one a client supplied would be a name it
    chose, and two different pictures could then claim one address.
    """
    settings = _settings(request)
    data = await request.body()

    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"at most {settings.max_upload_bytes} bytes",
        )

    content_type = sniff(data)
    if content_type is None:
        # Ruling 6: sniffed, never taken from the request, and SVG is
        # absent from the accepted set on purpose — §9.3 serves both
        # surfaces from one origin, so an SVG fetched by `<img>` from here
        # would be script running there.
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"expected one of {', '.join(SNIFFED_TYPES)}",
        )

    try:
        digest = await _store(request).put(data)
    except MediaUnavailable as unreachable:
        raise _unavailable(unreachable) from None

    return UploadedMediaBody(
        media_sha256=digest, content_type=content_type, bytes=len(data)
    )


@router.get("/{digest}")
async def fetch(digest: str, request: Request) -> Response:
    """No principal — ruling 5, and the module docstring says why."""
    if not _DIGEST.match(digest):
        # 404 rather than 422: a malformed address and an absent one are
        # the same fact to a caller, and answering them differently would
        # tell somebody guessing which of their guesses had the right
        # shape.
        raise _not_found()

    try:
        data = await _store(request).get(digest)
    except MediaUnavailable as unreachable:
        raise _unavailable(unreachable) from None

    if data is None:
        raise _not_found()

    content_type = sniff(data)
    if content_type is None:
        # Bytes that were acceptable at upload but are not now: the
        # accepted set was narrowed, or the object was replaced out of
        # band. Either way this must not be served as something the
        # browser will guess at.
        logger.warning("media %s is no longer a servable type; refusing", digest)
        raise _not_found()

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": _IMMUTABLE,
            # The type is sniffed and trustworthy, and this keeps the
            # browser from deciding otherwise (ruling 6).
            "X-Content-Type-Options": "nosniff",
        },
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such media")


def _unavailable(error: MediaUnavailable) -> HTTPException:
    """503, never 404. An outage that read as "that picture is missing"
    would send the operator looking for it in the library."""
    logger.warning("the object store is unavailable: %s", error)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="the media store is unavailable"
    )
