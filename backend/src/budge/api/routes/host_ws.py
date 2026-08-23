"""The operator's socket: frames out, commands in.

Ruling 9 is what shapes this module. `MatchHub.publish` may not await, so
it enqueues the raw `(base_seq, state, events)` triple and the projection
happens here — in *this* subscriber's writer task, where awaiting the
content directory is allowed and where a frame dropped under backpressure
is never projected at all.

The two directions run as separate tasks on purpose. A reader parked on
`receive_text` must not hold up a frame going out, and a writer projecting
a frame must not delay an acknowledgement: §9.2 puts the operator at about
one judgement every five seconds, looking at the players rather than at the
screen.
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from budge.api.hub import MatchHub, Subscriber
from budge.api.outcomes import ack_for, malformed_ack
from budge.api.principal import host_from_cookie
from budge.api.projection import project_host
from budge.api.schemas.commands import Ack, Envelope
from budge.api.services import Services
from budge.domain.events import Event
from budge.domain.ids import MatchId
from budge.domain.state import MatchState

logger = logging.getLogger(__name__)

router = APIRouter()

# 1008 is "policy violation", which is what the WebSocket protocol offers
# for "you are not allowed here". There is no 401 on this side of the
# handshake.
UNAUTHORISED = 1008
UNAVAILABLE = 1011


@router.websocket("/ws/host/{match_id}")
async def host_socket(websocket: WebSocket, match_id: UUID) -> None:
    """Authenticate *before* accepting.

    A socket accepted and then closed has already told the caller the
    handshake succeeded, and anything written between the two reaches them.
    Closing before accept means an unauthenticated caller receives nothing
    at all.
    """
    if host_from_cookie(websocket) is None:
        await websocket.close(code=UNAUTHORISED)
        return

    services: Services = websocket.app.state.services
    hub: MatchHub = websocket.app.state.hub
    match = MatchId(match_id)

    try:
        state = await services.lifecycle.state_of(match)
    except Exception:
        # A match id nobody created, or a log that will not fold. Either
        # way this socket has nothing to show, and saying so before accept
        # keeps the console from rendering an empty match.
        logger.warning("host socket: %s could not be loaded", match, exc_info=True)
        await websocket.close(code=UNAVAILABLE)
        return

    await websocket.accept()
    with hub.subscribe(match) as subscriber:
        # §7.2: a client that has just connected is a client with a gap,
        # and the answer to every gap is the whole state. Sent before the
        # writer starts, so the first thing the console sees cannot be an
        # incremental update that happened to arrive first.
        await _send_frame(websocket, services, state, ())

        writer = asyncio.create_task(_write(websocket, services, subscriber))
        reader = asyncio.create_task(_read(websocket, services, match))
        try:
            # Whichever ends first ends the socket. The reader ending is the
            # ordinary case — the operator closed the console. The writer
            # ending is not: it means a frame could not be projected or
            # sent, and closing is the right answer, because §7.2 makes a
            # reconnect free (the next socket opens with the whole state)
            # while a console left open behind a dead writer would show the
            # operator a match frozen at whatever it last received.
            await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
            if writer.done() and not writer.cancelled():
                failure = writer.exception()
                if failure is not None:
                    logger.warning(
                        "host socket for %s: the writer stopped; closing", match,
                        exc_info=failure,
                    )
        finally:
            for task in (reader, writer):
                task.cancel()
            for task in (reader, writer):
                # An exception from a socket that is already closing must
                # not replace the reason it closed.
                with suppress(asyncio.CancelledError, RuntimeError, WebSocketDisconnect):
                    await task


async def _send_frame(
    websocket: WebSocket,
    services: Services,
    state: MatchState,
    events: Sequence[Event],
) -> None:
    frame = await project_host(
        state, now=services.clock.now(), events=events, directory=services.directory
    )
    await websocket.send_text(frame.model_dump_json())


async def _write(websocket: WebSocket, services: Services, subscriber: Subscriber) -> None:
    """Project and send whatever this subscriber's queue yields.

    Ruling 9's payoff: two subscribers of one match project the same state
    twice, which at three subscribers is not worth a shared future — and a
    frame the queue dropped under backpressure costs no projection at all.
    """
    while True:
        update = await subscriber.next()
        await _send_frame(websocket, services, update.state, update.events)


async def _read(websocket: WebSocket, services: Services, match_id: MatchId) -> None:
    """Parse one envelope, submit it, acknowledge it — and never close on a
    bad frame."""
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            envelope = Envelope.model_validate_json(raw)
        except ValidationError as invalid:
            # A typo in a hand-written client must not cost the operator
            # their socket mid-match — and a malformed frame is its own
            # outcome, never a `rejected`, which would put a rules message
            # in front of the operator for what is a client bug.
            await _ack(websocket, malformed_ack(_correlation_of(raw), invalid.title))
            continue

        outcome = await services.gateway.submit(match_id, envelope.command.to_domain())
        await _ack(websocket, ack_for(outcome, correlation_id=envelope.correlation_id))


def _correlation_of(raw: str) -> str | None:
    """Best-effort echo for a frame that did not parse.

    A client correlating its own retries deserves the id back even when the
    rest of the frame was wrong, and this is the only place it can come
    from — the envelope never validated.
    """
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    value = loaded.get("correlation_id")
    return value if isinstance(value, str) else None


async def _ack(websocket: WebSocket, ack: Ack) -> None:
    await websocket.send_text(ack.model_dump_json())
