"""Everything six plans built, exercised as one sequence.

Each plan's own suite proves its layer against fakes at the seams. This
proves the seams themselves: a deal that draws from the real library under
§5.3's lock, a pack drawn at declaration, a stage frame naming the revealed
category and carrying no answer, and the host frame carrying one — all over
the real HTTP surface, against the real database.

It is the only test in the repository that touches every layer at once, and
it is worth its runtime for exactly that reason: every other test in the
suite would still pass against a seam that had never been connected.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from api.conftest import TEST_PASSWORD, TEST_SECRET, running_app
from podvinsya.api.app import build_app
from podvinsya.api.principal import SESSION_COOKIE
from podvinsya.api.security import mint_session, mint_stage_token
from podvinsya.api.settings import ApiSettings
from podvinsya.domain.ids import MatchId
from support.asgi import ASGIWebSocketClient
from support.walk import every_string

pytestmark = pytest.mark.integration

# A 3x4 board: twelve cells, two players. §3.4 puts one secret on each
# player's own cells, so ten ordinary categories are drawn and two secrets
# are assigned from the roster.
BOARD = {"width": 3, "height": 4}
CELLS = 12
PLAYERS = 2

ANSWER = "ОТВЕТ-{}-{}"
TITLE = "ТЕМА-{}"
SECRET_TITLE = "СЕКРЕТ-{}"


# A distinct, well-formed PNG per picture. Ruling 4 means the library will
# only name a digest the store holds, so the show's pictures are uploaded
# for real — which is also what makes this test cover the media seam.
def a_picture(seed: int) -> bytes:
    return b"\x89PNG\r\n\x1a\x0a" + seed.to_bytes(4, "big") + bytes(range(32))


async def upload(client: Any, data: bytes) -> str:
    response = await client.post("/api/media", content=data)
    assert response.status_code == 201, response.text
    digest: str = response.json()["media_sha256"]
    return digest


class Inbox:
    """Reads a socket without assuming ack-and-frame ordering.

    §6.2 publishes the frame *before* resolving the origin, so the frame is
    enqueued first — but the writer task and the reader are separate tasks,
    and which reaches the socket first is a scheduling detail. A console
    must not depend on it either, which is precisely what `kind` is for:
    every message this system sends says what it is.
    """

    def __init__(self, socket: ASGIWebSocketClient) -> None:
        self._socket = socket
        self._waiting: list[dict[str, Any]] = []

    async def next_of(self, kind: str) -> dict[str, Any]:
        for index, message in enumerate(self._waiting):
            if message["kind"] == kind:
                return self._waiting.pop(index)
        while True:
            message = await self._socket.receive_json()
            if message["kind"] == kind:
                return message
            self._waiting.append(message)

    async def ack(self) -> dict[str, Any]:
        return await self.next_of("ack")


async def stock_the_library(client: Any) -> tuple[list[str], list[str]]:
    """Ten ordinary categories and two secrets, three pictures each."""
    ordinary: list[str] = []
    secrets: list[str] = []
    for index in range(CELLS - PLAYERS):
        created = await client.post(
            "/api/library/categories", json={"title": TITLE.format(index), "is_secret": False}
        )
        assert created.status_code == 201, created.text
        ordinary.append(created.json()["id"])
    for index in range(PLAYERS):
        created = await client.post(
            "/api/library/categories",
            json={"title": SECRET_TITLE.format(index), "is_secret": True},
        )
        secrets.append(created.json()["id"])

    for position, category_id in enumerate(ordinary + secrets):
        for picture in range(3):
            added = await client.post(
                f"/api/library/categories/{category_id}/images",
                json={
                    "media_sha256": await upload(client, a_picture(position * 10 + picture)),
                    "answer_text": ANSWER.format(position, picture),
                },
            )
            assert added.status_code == 201, added.text
    return ordinary, secrets


async def test_a_match_can_be_played_from_an_empty_database(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Log in, stock a library, deal a board, declare an attack, judge.

    Kills on: any seam that was only ever tested against a fake — a bank
    whose `FOR SHARE` deadlocks against the appending transaction, a
    directory whose ids do not match what selection drew, a projection
    asking for a category the deal never created, a materialiser that
    draws a pack the domain then refuses.
    """
    app = build_app(api_settings)
    cookies = {SESSION_COOKIE: mint_session(TEST_SECRET, issued_at=datetime.now(UTC))}

    async with running_app(app) as client:
        assert (
            await client.post("/api/session", json={"password": TEST_PASSWORD})
        ).status_code == 204

        _ordinary, secrets = await stock_the_library(client)

        readiness = (await client.get(f"/api/library/readiness?cells={CELLS}")).json()
        assert readiness["ordinary_available"] == CELLS - PLAYERS
        assert readiness["secrets_available"] == PLAYERS
        # Three pictures each is well under the threshold: §8's soft
        # warning fires, and the show goes on anyway. That is the whole
        # meaning of «мягкое».
        assert len(readiness["thin"]) == CELLS
        assert readiness["ready"] is False

        created = await client.post(
            "/api/matches", json={"board": BOARD, "player_count": PLAYERS}
        )
        assert created.status_code == 201, created.text
        match_id = created.json()["match_id"]

        players = []
        for index, secret in enumerate(secrets):
            player_id = str(UUID(int=index + 1))
            players.append(player_id)
            assert (
                await client.post(
                    f"/api/matches/{match_id}/players",
                    json={
                        "player_id": player_id,
                        "name": f"Игрок {index + 1}",
                        "colour": ["#e5484d", "#3b82f6"][index],
                    },
                )
            ).json()["outcome"] == "accepted"
            assert (
                await client.post(
                    f"/api/matches/{match_id}/secrets",
                    json={"player_id": player_id, "category": secret},
                )
            ).json()["outcome"] == "accepted"

        dealt = await client.post(f"/api/matches/{match_id}/deal")
        assert dealt.status_code == 200, dealt.text
        assert dealt.json()["outcome"] == "accepted"

        started = await client.post(f"/api/matches/{match_id}/start")
        assert started.status_code == 200, started.text

        # ---- both surfaces, on the state the deal produced -------------
        token = mint_stage_token(TEST_SECRET, MatchId(UUID(match_id)))
        async with (
            ASGIWebSocketClient(app, f"/ws/host/{match_id}", cookies=cookies) as host,
            ASGIWebSocketClient(app, f"/ws/stage/{token}") as stage,
        ):
            host_inbox, stage_inbox = Inbox(host), Inbox(stage)
            host_frame = await host_inbox.next_of("host")
            stage_frame = await stage_inbox.next_of("stage")

            assert host_frame["status"] == "running"
            assert len(host_frame["groups"]) == CELLS
            assert len(stage_frame["groups"]) == CELLS

            # Every ordinary group is revealed and named on both surfaces;
            # the two secrets are named for the operator and hidden from
            # the room. §7.1, end to end.
            named_on_stage = [
                group["category"]["name"]
                for group in stage_frame["groups"]
                if group["category"]["kind"] == "named"
            ]
            hidden_on_stage = [
                group for group in stage_frame["groups"] if group["category"]["kind"] == "hidden"
            ]
            assert len(hidden_on_stage) == PLAYERS
            assert len(named_on_stage) == CELLS - PLAYERS
            assert all(name.startswith("ТЕМА-") for name in named_on_stage)

            host_names = [group["category"]["name"] for group in host_frame["groups"]]
            assert sum(name.startswith("СЕКРЕТ-") for name in host_names) == PLAYERS
            assert all(name is not None for name in host_names)

            # §11's «Проекции» row, on a frame that came off a socket and
            # out of a real library.
            assert not [s for s in every_string(stage_frame) if s.startswith("ОТВЕТ-")]
            assert not [s for s in every_string(stage_frame) if s.startswith("СЕКРЕТ-")]

            # ---- declare, start, judge ---------------------------------
            attacker = host_frame["current_player"]
            attacking, defending = next(
                (group_id, targets[0])
                for group_id, targets in host_frame["legal_attacks"].items()
                if targets
            )
            assert attacking in {
                group["id"] for group in host_frame["groups"] if group["owner"] == attacker
            }

            await host.send_json(
                {
                    "correlation_id": "declare",
                    "command": {
                        "type": "declare_attack",
                        "attacking_group": attacking,
                        "defending_group": defending,
                    },
                }
            )
            assert (await host_inbox.ack())["outcome"] == "accepted"

            declared_host = await host_inbox.next_of("host")
            declared_stage = await stage_inbox.next_of("stage")

            assert declared_host["duel"] is not None
            assert declared_stage["duel"] is not None
            # §3.5: the whole pack is drawn at declaration, so the screen
            # can preload it while the operator explains the category.
            assert len(declared_stage["duel"]["image_order"]) == 3
            assert declared_stage["duel"]["image_order"] == declared_host["duel"]["image_order"]
            assert declared_host["duel"]["current_answer"].startswith("ОТВЕТ-")
            assert not [s for s in every_string(declared_stage) if s.startswith("ОТВЕТ-")]

            await host.send_json({"command": {"type": "start_duel"}})
            assert (await host_inbox.ack())["outcome"] == "accepted"
            running_host = await host_inbox.next_of("host")
            await stage_inbox.next_of("stage")

            assert running_host["duel"]["phase"] == "running"
            assert running_host["duel"]["timing"]["deadline_at"] is not None
            assert len(running_host["duel"]["timing"]["remaining_ms"]) == PLAYERS

            await host.send_json({"command": {"type": "judge_correct"}})
            assert (await host_inbox.ack())["outcome"] == "accepted"
            judged = await host_inbox.next_of("host")

            assert judged["duel"]["index"] == 1
            assert judged["last_event_types"] == ["duel.answer_accepted"]
            assert judged["duel"]["current_answer"].startswith("ОТВЕТ-")
            assert judged["duel"]["current_answer"] != declared_host["duel"]["current_answer"]

        # ---- and the log survived it ------------------------------------
        snapshot = await client.get(f"/api/matches/{match_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["frame"]["seq"] == judged["seq"]

        listed = (await client.get("/api/matches")).json()
        assert [row["status"] for row in listed] == ["running"]
        assert len(listed[0]["players"]) == PLAYERS


async def test_a_category_played_once_is_not_dealt_again(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """§8: «Отбор на партию: из активных, без повторов». A re-deal draws a
    fresh selection, and every cell still gets a distinct category.

    Kills on: sampling with replacement, which breaks §2.8's bijection
    between groups and unplayed categories — and is invisible until a board
    happens to draw a duplicate."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        await client.post("/api/session", json={"password": TEST_PASSWORD})
        _ordinary, secrets = await stock_the_library(client)

        created = await client.post(
            "/api/matches", json={"board": BOARD, "player_count": PLAYERS}
        )
        match_id = created.json()["match_id"]
        for index, secret in enumerate(secrets):
            player_id = str(UUID(int=index + 1))
            await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": player_id, "name": f"P{index}", "colour": "#111111"},
            )
            await client.post(
                f"/api/matches/{match_id}/secrets",
                json={"player_id": player_id, "category": secret},
            )

        # §3.4: «Повторный `DealBoard` — это и есть кнопка "перераздать"».
        assert (await client.post(f"/api/matches/{match_id}/deal")).status_code == 200
        assert (await client.post(f"/api/matches/{match_id}/deal")).status_code == 200

        snapshot = (await client.get(f"/api/matches/{match_id}")).json()

    categories = [group["category"]["id"] for group in snapshot["frame"]["groups"]]
    assert len(categories) == CELLS
    assert len(set(categories)) == CELLS
