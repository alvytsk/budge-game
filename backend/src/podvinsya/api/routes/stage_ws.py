"""The room's socket. Frames out, and nothing in.

§7.5: «Экран сцены … получает **только read-only подписку** — отправить
команду он не может конструктивно.» Ruling 12 makes «конструктивно» a
property of this module rather than of a runtime check, in two ways:

*Imports.* Nothing here imports `CommandGateway`, `MatchManager` or any
domain `Command`. There is no name in this scope that could submit
anything, and
`test_the_stage_module_imports_no_way_to_submit_a_command` reads this
file's own imports to hold that line.

*Values.* The object this module is handed is a `ReadOnlyMatches`, not the
full `Services` — which carries a `.gateway`, one attribute away from a
command. A read-only surface holding a handle to a writer is not one.

Nothing this socket receives is ever *interpreted*. `_drain_until_disconnect`
reads the ASGI channel and discards everything on it, looking only for the
one message that says the peer has gone. That is not a command path — the
bytes are never parsed, never validated and never dispatched — and it is
what keeps a closed browser from leaving a subscriber attached to a live
match for the rest of the process's life.
"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from podvinsya.api.hub import MatchHub, Subscriber
from podvinsya.api.principal import stage_principal_for
from podvinsya.api.projection import project_stage
from podvinsya.api.services import ReadOnlyMatches
from podvinsya.api.settings import ApiSettings
from podvinsya.domain.events import Event
from podvinsya.domain.state import MatchState

logger = logging.getLogger(__name__)

router = APIRouter()

UNAUTHORISED = 1008
UNAVAILABLE = 1011


@router.websocket("/ws/stage/{token}")
async def stage_socket(websocket: WebSocket, token: str) -> None:
    """The token names the match (ruling 7): derived, not stored, and bound
    to exactly one match — so last week's link cannot watch tonight's game.

    Refused before accept, for the same reason the host socket is: a socket
    accepted and then closed has already said the handshake succeeded.
    """
    settings: ApiSettings = websocket.app.state.settings
    principal = stage_principal_for(settings, token)
    if principal is None:
        await websocket.close(code=UNAUTHORISED)
        return

    matches: ReadOnlyMatches = websocket.app.state.read_only
    hub: MatchHub = websocket.app.state.hub

    try:
        state = await matches.state_of(principal.match_id)
    except Exception:
        logger.warning("stage socket: %s could not be loaded", principal.match_id, exc_info=True)
        await websocket.close(code=UNAVAILABLE)
        return

    await websocket.accept()
    with hub.subscribe(principal.match_id) as subscriber:
        # §7.2, the same as the host's: the answer to a gap is the whole
        # state, and a screen that has just connected has the largest gap
        # there is.
        await _send_frame(websocket, matches, state, ())

        writer = asyncio.create_task(_write(websocket, matches, subscriber))
        watcher = asyncio.create_task(_drain_until_disconnect(websocket))
        try:
            # Whichever finishes first ends the socket: the writer stops
            # when the send fails, the watcher when the peer goes away. An
            # idle match sends nothing for minutes at a time, so without
            # the watcher a closed browser would leave this subscriber
            # attached, being offered frames nobody will ever read, until
            # the process stopped.
            await asyncio.wait(
                {writer, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (writer, watcher):
                task.cancel()
            for task in (writer, watcher):
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await task


async def _drain_until_disconnect(websocket: WebSocket) -> None:
    """Read the channel and throw everything away.

    This is not a reader in the sense §7.5 forbids. Nothing here parses a
    frame, and there is no name in this module that could act on one if it
    did: the only thing this loop reacts to is the transport's own
    `websocket.disconnect`. A screen that sends a command gets exactly what
    §7.5 promises — silence, and no effect.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


async def _send_frame(
    websocket: WebSocket,
    matches: ReadOnlyMatches,
    state: MatchState,
    events: Sequence[Event],
) -> None:
    frame = await project_stage(
        state, now=matches.clock.now(), events=events, directory=matches.directory
    )
    await websocket.send_text(frame.model_dump_json())


async def _write(
    websocket: WebSocket, matches: ReadOnlyMatches, subscriber: Subscriber
) -> None:
    """The only loop in this module.

    There is no reader task beside it. §7.5's read-only subscription is not
    a check that refuses commands — it is the absence of anywhere for a
    command to go.
    """
    while True:
        update = await subscriber.next()
        await _send_frame(websocket, matches, update.state, update.events)
