"""The operator's socket, driven through ASGI on the test's own loop.

`ASGIWebSocketClient` rather than Starlette's `TestClient`: the latter runs
the application on a second loop in a background thread, and half the
properties here are about ordering between a command and the frame it
causes.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from api.conftest import TEST_SECRET, running_app
from podvinsya.api.app import build_app
from podvinsya.api.principal import SESSION_COOKIE
from podvinsya.api.routes.host_ws import UNAUTHORISED
from podvinsya.api.security import mint_session
from podvinsya.api.settings import ApiSettings
from podvinsya.domain.ids import MatchId
from support.asgi import ASGIWebSocketClient, WebSocketRejected

pytestmark = pytest.mark.integration

BOARD = {"width": 3, "height": 4}


def session_cookie() -> dict[str, str]:
    return {SESSION_COOKIE: mint_session(TEST_SECRET, issued_at=datetime.now(UTC))}


async def create_match(client: Any) -> str:
    response = await client.post("/api/matches", json={"board": BOARD, "player_count": 2})
    assert response.status_code == 201, response.text
    match_id: str = response.json()["match_id"]
    return match_id


async def test_an_unauthenticated_socket_is_refused_before_it_is_accepted(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Kills on: `accept()` before the cookie check. An accepted-then-closed
    socket has already told the caller the handshake succeeded, and anything
    written between the two reaches them."""
    app = build_app(api_settings)
    async with running_app(app):
        with pytest.raises(WebSocketRejected) as refused:
            async with ASGIWebSocketClient(app, f"/ws/host/{uuid4()}"):
                pass

    assert refused.value.code == UNAUTHORISED


async def test_a_forged_cookie_is_refused(clean_db: None, api_settings: ApiSettings) -> None:
    """Kills on: trusting the cookie's presence rather than its signature."""
    app = build_app(api_settings)
    forged = {SESSION_COOKIE: mint_session("another-key", issued_at=datetime.now(UTC))}
    async with running_app(app):
        with pytest.raises(WebSocketRejected):
            async with ASGIWebSocketClient(app, f"/ws/host/{uuid4()}", cookies=forged):
                pass


async def test_a_match_that_does_not_exist_is_refused(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Kills on: accepting first and letting `MatchNotFound` escape the
    writer task, which leaves the console rendering an empty match."""
    app = build_app(api_settings)
    async with running_app(app):
        with pytest.raises(WebSocketRejected):
            async with ASGIWebSocketClient(app, f"/ws/host/{uuid4()}", cookies=session_cookie()):
                pass


async def test_a_connected_host_receives_a_full_frame_immediately(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§7.2: a client that has just connected is a client with a gap, and
    the answer to every gap is the whole state.

    Kills on: waiting for the next command before sending anything, which
    leaves a reconnecting console blank until the operator presses
    something."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)

        async with ASGIWebSocketClient(
            app, f"/ws/host/{match_id}", cookies=session_cookie()
        ) as socket:
            frame = await socket.receive_json()

    assert frame["kind"] == "host"
    assert frame["match_id"] == match_id
    assert frame["seq"] == 1
    assert frame["status"] == "setup"


async def test_a_command_reaches_the_match_and_is_acknowledged(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Ruling 5's live half, and the ack vocabulary `outcomes.py` fixes."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)

        async with ASGIWebSocketClient(
            app, f"/ws/host/{match_id}", cookies=session_cookie()
        ) as socket:
            await socket.receive_json()  # the opening frame
            await socket.send_json(
                {"correlation_id": "console-1", "command": {"type": "start_duel"}}
            )
            reply = await socket.receive_json()

    # `StartDuel` in SETUP is a legal command in the wrong phase, so the
    # domain refuses it — which is exactly the round trip under test.
    assert reply["kind"] == "ack"
    assert reply["outcome"] == "rejected"
    assert reply["reason"] == "wrong_status"
    assert reply["correlation_id"] == "console-1"


async def test_a_frame_arrives_when_the_match_advances(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """The hub, the writer task and the projection, end to end.

    Kills on: never starting the writer task, which leaves the console
    showing the state it connected with for the whole match."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)

        async with ASGIWebSocketClient(
            app, f"/ws/host/{match_id}", cookies=session_cookie()
        ) as socket:
            first = await socket.receive_json()

            added = await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": str(uuid4()), "name": "Аня", "colour": "#e5484d"},
            )
            assert added.status_code == 200

            second = await socket.receive_json()

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert [player["name"] for player in second["players"]] == ["Аня"]
    assert second["last_event_types"] == ["match.player_added"]


async def test_a_malformed_frame_is_acked_and_the_socket_stays_open(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Kills on: letting the `ValidationError` propagate, which closes the
    socket — a typo in a hand-written client would then cost the operator
    their console mid-match."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)

        async with ASGIWebSocketClient(
            app, f"/ws/host/{match_id}", cookies=session_cookie()
        ) as socket:
            await socket.receive_json()
            await socket.send_json({"correlation_id": "c9", "command": {"type": "judge_wrong"}})
            bad = await socket.receive_json()

            await socket.send_json({"command": {"type": "pause_duel"}})
            good = await socket.receive_json()

    assert bad["outcome"] == "malformed"
    assert bad["correlation_id"] == "c9"
    assert good["kind"] == "ack"
    assert good["outcome"] == "rejected"


async def test_a_frame_that_names_a_sender_is_refused(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§7.4, over the live transport. Kills on: dropping `extra="forbid"`,
    which would serve the command with the actor field silently dropped —
    and the client would believe it had been honoured."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)

        async with ASGIWebSocketClient(
            app, f"/ws/host/{match_id}", cookies=session_cookie()
        ) as socket:
            await socket.receive_json()
            await socket.send_json(
                {"command": {"type": "judge_correct", "player_id": str(uuid4())}}
            )
            reply = await socket.receive_json()

    assert reply["outcome"] == "malformed"


async def test_a_disconnecting_host_is_unsubscribed(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Kills on: subscribing outside a context manager, which leaks a queue
    per reconnect for the life of the process — and every one of them keeps
    being offered frames nobody will ever read."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        hub = app.state.hub
        match = MatchId(UUID(match_id))
        async with ASGIWebSocketClient(
            app, f"/ws/host/{match_id}", cookies=session_cookie()
        ) as socket:
            await socket.receive_json()
            assert hub.subscriber_count(match) == 1

        assert hub.subscriber_count(match) == 0


async def test_two_consoles_of_one_match_both_receive(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Two operator windows, or one reconnecting over a stale socket — both
    happen, and §6.1's hub is what makes them equivalent."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)

        async with (
            ASGIWebSocketClient(app, f"/ws/host/{match_id}", cookies=session_cookie()) as first,
            ASGIWebSocketClient(app, f"/ws/host/{match_id}", cookies=session_cookie()) as second,
        ):
            await first.receive_json()
            await second.receive_json()

            await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": str(uuid4()), "name": "B", "colour": "#3b82f6"},
            )

            assert (await first.receive_json())["seq"] == 2
            assert (await second.receive_json())["seq"] == 2
