"""Ruling 5's REST half, end to end: assemble a match, list it, load it.

Every test drives the real app against the real database. The routes are
thin — one gateway call and one mapping — so what is actually under test is
the wiring: that each route reaches the command it claims to, that each one
is guarded, and that the answers use the domain's own vocabulary.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest

from api.conftest import TEST_PASSWORD, running_app
from budge.api.app import build_app
from budge.api.security import read_stage_token
from budge.api.settings import ApiSettings
from budge.domain.ids import MatchId

pytestmark = pytest.mark.integration

BOARD = {"width": 3, "height": 4}


async def log_in(client: Any) -> None:
    """Authenticate the shared client. httpx keeps the cookie jar, so every
    later call on this client carries the session the way a browser would."""
    response = await client.post("/api/session", json={"password": TEST_PASSWORD})
    assert response.status_code == 204


async def create_match(client: Any, *, player_count: int = 2) -> tuple[str, str]:
    response = await client.post(
        "/api/matches", json={"board": BOARD, "player_count": player_count}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["match_id"], body["stage_token"]


async def started_match(client: Any) -> str:
    """A match run through to RUNNING over the same routes the console uses:
    two players, each with a secret, `deal`, `start`.

    No pictures uploaded: `test_a_whole_match.py` already shows `deal`
    succeeding with a library `readiness` reports as not `ready` — pictures
    are §8's soft warning, not a requirement `deal` enforces. `BOARD` is
    3x4 (twelve cells), so ten ordinary categories fill the rest once two
    are spent on secrets.
    """
    match_id, _token = await create_match(client)
    secrets = []
    for index in range(2):
        created = await client.post(
            "/api/library/categories", json={"title": f"Секрет {index}", "is_secret": True}
        )
        assert created.status_code == 201, created.text
        secrets.append(created.json()["id"])
    for index in range(10):
        created = await client.post(
            "/api/library/categories", json={"title": f"Тема {index}", "is_secret": False}
        )
        assert created.status_code == 201, created.text
    for index, secret in enumerate(secrets):
        player_id = str(UUID(int=index + 1))
        added = await client.post(
            f"/api/matches/{match_id}/players",
            json={"player_id": player_id, "name": f"P{index}", "colour": "#e4572e"},
        )
        assert added.status_code == 200, added.text
        assigned = await client.post(
            f"/api/matches/{match_id}/secrets",
            json={"player_id": player_id, "category": secret},
        )
        assert assigned.status_code == 200, assigned.text
    dealt = await client.post(f"/api/matches/{match_id}/deal")
    assert dealt.status_code == 200, dealt.text
    started = await client.post(f"/api/matches/{match_id}/start")
    assert started.status_code == 200, started.text
    return match_id


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/matches", {"board": BOARD, "player_count": 2}),
        (
            "POST",
            "/api/matches/{id}/players",
            {"player_id": str(uuid4()), "name": "A", "colour": "#1"},
        ),
        (
            "POST",
            "/api/matches/{id}/secrets",
            {"player_id": str(uuid4()), "category": str(uuid4())},
        ),
        ("POST", "/api/matches/{id}/deal", None),
        ("POST", "/api/matches/{id}/start", None),
        ("POST", "/api/matches/{id}/reset", {"keep_roster": True}),
        ("GET", "/api/matches", None),
        ("GET", "/api/matches/{id}", None),
    ],
)
async def test_every_match_route_refuses_an_unauthenticated_caller(
    api_settings: ApiSettings, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Parametrized over all eight on purpose.

    Kills on: forgetting the dependency on exactly one route — which is the
    failure a per-route test set is least likely to catch, because the test
    for the forgotten route is the one that was never written."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.request(method, path.format(id=uuid4()), json=body)

    assert response.status_code == 401, f"{method} {path} let an anonymous caller through"


async def test_a_match_can_be_assembled_and_started_end_to_end(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """The whole of ruling 5's REST half, in the order §3.3 requires.

    `deal` is expected to refuse: the content library is plan 6's, and
    `UnavailableCategories` refuses a draw as §6.3's ordinary rejection.
    Asserting that here is what keeps this test honest about what the
    server can actually do today."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)

        for index in range(2):
            player = str(uuid4())
            added = await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": player, "name": f"P{index}", "colour": "#112233"},
            )
            assert added.status_code == 200
            assert added.json()["outcome"] == "accepted"

            secret = await client.post(
                f"/api/matches/{match_id}/secrets",
                json={"player_id": player, "category": str(uuid4())},
            )
            assert secret.status_code == 200

        dealt = await client.post(f"/api/matches/{match_id}/deal")
        assert dealt.status_code == 409
        assert dealt.json()["reason"] == "content_unavailable"

        started = await client.post(f"/api/matches/{match_id}/start")
        assert started.status_code == 409
        assert started.json()["reason"] == "deal_invalid"


async def test_an_invalid_board_is_a_409_carrying_the_domain_s_reason(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Ruling 4: the API re-validates nothing, and the reason the operator
    sees is `validate_board`'s own.

    Kills on: adding a Pydantic constraint that duplicates the domain — the
    status would become 422 and the reason would be Pydantic's, which is a
    second vocabulary for one rule."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        response = await client.post(
            "/api/matches", json={"board": {"width": 3, "height": 5}, "player_count": 2}
        )

    assert response.status_code == 409
    assert response.json()["reason"] == "board_not_divisible"


async def test_a_refused_creation_leaves_no_match_behind(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Kills on: writing genesis before deciding — the list would then show
    a match that no legal log explains."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        await client.post(
            "/api/matches", json={"board": {"width": 2, "height": 2}, "player_count": 2}
        )
        listed = await client.get("/api/matches")

    assert listed.json() == []


async def test_the_list_shows_what_the_read_model_holds(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Ruling 14, over the wire."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)
        await client.post(
            f"/api/matches/{match_id}/players",
            json={"player_id": str(uuid4()), "name": "Аня", "colour": "#e5484d"},
        )

        listed = await client.get("/api/matches")

    rows = listed.json()
    assert [row["id"] for row in rows] == [match_id]
    assert rows[0]["status"] == "setup"
    assert rows[0]["players"] == [{"name": "Аня", "colour": "#e5484d", "eliminated": False}]


async def test_the_snapshot_is_the_host_frame_the_socket_would_send(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§7.4's «снапшот для первичной загрузки», built by the same
    `project_host`.

    Kills on: hand-rolling a second snapshot shape, which would give the
    console two payloads to handle for one state."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)

        response = await client.get(f"/api/matches/{match_id}")

    assert response.status_code == 200
    frame = response.json()["frame"]
    assert frame["kind"] == "host"
    assert frame["match_id"] == match_id
    assert frame["status"] == "setup"
    assert frame["seq"] == 1
    assert frame["server_now"] is not None
    assert frame["legal_attacks"] == {}


async def test_the_snapshot_carries_the_same_stage_token_creation_did(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Ruling 7: derived, not stored — so the same match yields the same
    token every time it is asked for, and losing the creation response is
    recoverable.

    Kills on: minting from anything but the match id (a timestamp, a
    counter), which would strand a stage screen whose link was printed
    before a restart."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, token = await create_match(client)

        response = await client.get(f"/api/matches/{match_id}")

    assert response.json()["stage_token"] == token
    assert read_stage_token(api_settings.secret_key, token) == MatchId(UUID(match_id))


async def test_a_snapshot_of_a_match_that_does_not_exist_is_a_404(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Kills on: letting `MatchNotFound` escape, which turns a mistyped URL
    into a 500 with a stack trace."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        response = await client.get(f"/api/matches/{uuid4()}")

    assert response.status_code == 404


async def test_an_unknown_field_in_a_body_is_refused(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§7.4 again: a body that named a sender must be refused, not served
    with the field quietly dropped."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)
        response = await client.post(
            f"/api/matches/{match_id}/players",
            json={
                "player_id": str(uuid4()),
                "name": "A",
                "colour": "#1",
                "as_player": str(uuid4()),
            },
        )

    assert response.status_code == 422


async def test_dealing_twice_is_the_redeal_button(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§3.4: «Повторный `DealBoard` — это и есть кнопка "перераздать"», so
    the route is deliberately repeatable.

    Kills on: making it idempotent — a second POST answering 409 «already
    dealt» would remove the one control §9.2 puts on the setup screen."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)
        first = await client.post(f"/api/matches/{match_id}/deal")
        second = await client.post(f"/api/matches/{match_id}/deal")

    # Both refuse for the same reason — no players added yet — rather than
    # the second refusing because the first happened.
    assert first.json()["reason"] == second.json()["reason"] == "player_count_invalid"


async def test_reset_returns_a_running_match_to_setup(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§A.8: an assembly command, so REST — beside `deal` and `start`.

    Kills on: a route that sent the command past the gateway — the runtime
    would never learn of the reset, and the next frame would still show a
    board that no longer exists.
    """
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id = await started_match(client)

        response = await client.post(
            f"/api/matches/{match_id}/reset", json={"keep_roster": True}
        )
        assert response.status_code == 200
        assert response.json()["outcome"] == "accepted"

        snapshot = (await client.get(f"/api/matches/{match_id}")).json()

    assert snapshot["frame"]["status"] == "setup"
    assert snapshot["frame"]["groups"] == []
    assert len(snapshot["frame"]["players"]) == 2


async def test_a_full_reset_drops_the_roster(clean_db: None, api_settings: ApiSettings) -> None:
    """`keep_roster: false` answers §A.8's other question: start from
    scratch, not just this same match again."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id = await started_match(client)

        response = await client.post(
            f"/api/matches/{match_id}/reset", json={"keep_roster": False}
        )
        assert response.status_code == 200

        snapshot = (await client.get(f"/api/matches/{match_id}")).json()

    assert snapshot["frame"]["players"] == []


async def test_resetting_a_fresh_match_is_a_noop_and_not_an_error(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """Ruling 4, through `outcomes.py`: an empty transition is a 200 `noop`,
    not a 409.

    Kills on: a refusal in place of the empty event — an operator who
    pressed "Reset" twice would see an error for having gotten what they
    wanted.
    """
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)

        response = await client.post(
            f"/api/matches/{match_id}/reset", json={"keep_roster": False}
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "noop"


async def test_reset_refuses_a_body_it_does_not_understand(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """`Body` is declared `extra="forbid"`: a client that thought it said
    something must not get a 200."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        match_id, _token = await create_match(client)

        response = await client.post(
            f"/api/matches/{match_id}/reset",
            json={"keep_roster": True, "wipe_library": True},
        )

    assert response.status_code == 422
