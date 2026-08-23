"""§7.5's read-only surface, and the two ways this plan makes it read-only.

The first test in this module is the one that matters most: it reads
`stage_ws.py`'s own imports and asserts there is no name in that module
capable of submitting a command. That is what ruling 12 buys — «отправить
команду он не может конструктивно» becomes something a test can check,
rather than a property somebody has to keep remembering.
"""

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from api.conftest import TEST_SECRET, running_app
from podvinsya.api.app import build_app
from podvinsya.api.principal import SESSION_COOKIE
from podvinsya.api.routes import stage_ws
from podvinsya.api.routes.stage_ws import UNAUTHORISED
from podvinsya.api.security import mint_session, mint_stage_token
from podvinsya.api.services import ReadOnlyMatches, Services
from podvinsya.api.settings import ApiSettings
from podvinsya.domain.ids import MatchId
from support.asgi import ASGIWebSocketClient, WebSocketRejected
from support.walk import every_string

BOARD = {"width": 3, "height": 4}

# Every name that could put a command into a match. None of them may be
# importable from the stage's module.
FORBIDDEN_IMPORTS = ("CommandGateway", "MatchManager", "MatchLifecycle", "Services")
FORBIDDEN_MODULES = ("podvinsya.domain.actions", "podvinsya.runtime")


def test_the_stage_module_imports_no_way_to_submit_a_command() -> None:
    """§7.5's «конструктивно», checked.

    Kills on: importing the gateway "just to look at the match" — which is
    exactly how a read-only surface stops being one, and how it stops being
    obvious in review that it has."""
    source = Path(stage_ws.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_names: set[str] = set()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)

    assert not (imported_names & set(FORBIDDEN_IMPORTS))
    assert not [
        module
        for module in imported_modules
        for forbidden in FORBIDDEN_MODULES
        if module == forbidden or module.startswith(forbidden + ".")
    ]


def test_the_forbidden_import_list_is_not_vacuous() -> None:
    """A typo in either list would make the test above pass against a module
    that imported everything. The host's socket legitimately imports what
    the stage's may not, so it is the control."""
    source = Path(stage_ws.__file__).parent.joinpath("host_ws.py").read_text(encoding="utf-8")
    names = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert names & set(FORBIDDEN_IMPORTS)


def test_the_read_only_view_has_no_handle_on_a_writer() -> None:
    """Ruling 12's second half: values, not only imports.

    Kills on: handing the stage route the full `Services`, whose `.gateway`
    is one attribute access away from submitting a command."""
    assert "gateway" not in ReadOnlyMatches.__slots__
    assert "lifecycle" not in ReadOnlyMatches.__slots__
    assert "manager" not in ReadOnlyMatches.__slots__
    assert "gateway" in Services.__slots__  # the control


# The three tests above read source and class definitions and need no
# database; every test below drives a real socket against a real match, and
# carries `integration` individually rather than through a module-level
# `pytestmark` that would drag the first three into the slow lane too.


def session_cookie() -> dict[str, str]:
    return {SESSION_COOKIE: mint_session(TEST_SECRET, issued_at=datetime.now(UTC))}


async def create_match(client: Any) -> str:
    response = await client.post("/api/matches", json={"board": BOARD, "player_count": 2})
    assert response.status_code == 201, response.text
    match_id: str = response.json()["match_id"]
    return match_id


@pytest.mark.integration
async def test_a_bad_token_is_refused_before_accept(
    clean_db: None, api_settings: ApiSettings
) -> None:
    app = build_app(api_settings)
    async with running_app(app):
        with pytest.raises(WebSocketRejected) as refused:
            async with ASGIWebSocketClient(app, "/ws/stage/not-a-token"):
                pass

    assert refused.value.code == UNAUTHORISED


@pytest.mark.integration
async def test_a_session_cookie_is_not_a_stage_link(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§7.5: the two tokens decide which of two projections to build, so a
    host cookie must not open a stage socket and the reverse.

    Kills on: dropping the subject prefix, which would make the operator's
    own cookie a valid stage link — harmless — *and* a stage link a valid
    session, which is not."""
    app = build_app(api_settings)
    token = mint_session(TEST_SECRET, issued_at=datetime.now(UTC))
    async with running_app(app):
        with pytest.raises(WebSocketRejected):
            async with ASGIWebSocketClient(app, f"/ws/stage/{token}"):
                pass


@pytest.mark.integration
async def test_a_token_for_one_match_does_not_open_another_s_socket(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Ruling 7: bound to exactly one match, so last week's printed link
    cannot watch tonight's game.

    Kills on: signing a constant instead of the match id."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        await create_match(client)
        elsewhere = mint_stage_token(TEST_SECRET, MatchId(uuid4()))

        with pytest.raises(WebSocketRejected):
            async with ASGIWebSocketClient(app, f"/ws/stage/{elsewhere}"):
                pass


@pytest.mark.integration
async def test_the_stage_receives_a_full_frame_immediately(
    clean_db: None, api_settings: ApiSettings
) -> None:
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))

        async with ASGIWebSocketClient(app, f"/ws/stage/{token}") as socket:
            frame = await socket.receive_json()

    assert frame["kind"] == "stage"
    assert frame["match_id"] == match_id
    assert frame["seq"] == 1


@pytest.mark.integration
async def test_the_stage_frame_carries_no_answer_and_no_unrevealed_name(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§11's «Проекции» row, this time over what actually went down the
    socket rather than over what the projection returned.

    The library is `UnavailableContent` today, so there is no name and no
    answer to leak — what this asserts is the *shape*: every category on
    the stage frame is `hidden`, which is ruling 3's leak-proof default and
    the state a future directory has to opt out of one group at a time.

    Kills on: serialising the host frame down this socket, whose categories
    carry an id and a name."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))

        async with ASGIWebSocketClient(app, f"/ws/stage/{token}") as socket:
            frame = await socket.receive_json()

    assert "legal_attacks" not in frame
    assert "current_answer" not in every_string(frame)
    for group in frame["groups"]:
        assert group["category"] == {"kind": "hidden"}


@pytest.mark.integration
async def test_a_command_sent_to_the_stage_changes_nothing(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """The behavioural pair of the import-graph test.

    A well-formed host command goes down the stage socket, and the match is
    unchanged afterwards. Kills on: adding a reader task to `stage_ws` —
    which the import test would also catch, but only if the reader used an
    import rather than reaching through `websocket.app.state`."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))

        async with ASGIWebSocketClient(app, f"/ws/stage/{token}") as socket:
            await socket.receive_json()
            await socket.send_json(
                {"command": {"type": "start_duel"}, "correlation_id": "sneaky"}
            )
            # No reply, and no frame: nothing happened. The read below is
            # what proves the socket did not answer — a reader would ack.
            snapshot = await client.get(f"/api/matches/{match_id}")

    assert snapshot.json()["frame"]["seq"] == 1
    assert snapshot.json()["frame"]["status"] == "setup"


@pytest.mark.integration
async def test_the_stage_sees_the_match_advance(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Read-only is not write-only: the screen still has to follow along."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))

        async with ASGIWebSocketClient(app, f"/ws/stage/{token}") as socket:
            assert (await socket.receive_json())["seq"] == 1

            await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": str(uuid4()), "name": "Аня", "colour": "#e5484d"},
            )

            second = await socket.receive_json()

    assert second["seq"] == 2
    assert [player["name"] for player in second["players"]] == ["Аня"]


@pytest.mark.integration
async def test_a_screen_that_goes_away_is_let_go_of_promptly(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """A closed browser must not leave a subscriber attached.

    Kills on: dropping `_drain_until_disconnect` and awaiting only the
    writer. An idle match sends nothing for minutes at a time, so the
    writer would park on its queue and this socket's subscriber would keep
    being offered frames nobody reads until the process stopped — and the
    close would only be noticed on the next frame, which may never come.

    The bound is what makes it a test: the teardown below returns within
    a second, where a leaking implementation waits for the client's own
    five-second timeout and only then cancels."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))
        hub = app.state.hub

        socket = ASGIWebSocketClient(app, f"/ws/stage/{token}")
        await socket.__aenter__()
        await socket.receive_json()
        assert hub.subscriber_count(MatchId(UUID(match_id))) == 1

        loop = asyncio.get_running_loop()
        started = loop.time()
        await socket.__aexit__(None, None, None)
        elapsed = loop.time() - started

    assert elapsed < 1.0, "the socket was only released by the client's timeout"
    assert hub.subscriber_count(MatchId(UUID(match_id))) == 0


@pytest.mark.integration
async def test_a_disconnecting_screen_is_unsubscribed(
    clean_db: None, api_settings: ApiSettings
) -> None:
    app = build_app(api_settings)
    async with running_app(app) as client:
        client.cookies.update(session_cookie())
        match_id = await create_match(client)
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))
        hub = app.state.hub

        async with ASGIWebSocketClient(app, f"/ws/stage/{token}") as socket:
            await socket.receive_json()
            assert hub.subscriber_count(MatchId(UUID(match_id))) == 1

        assert hub.subscriber_count(MatchId(UUID(match_id))) == 0
