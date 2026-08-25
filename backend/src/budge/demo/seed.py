"""§G: the demo, as a sequence of calls to the real API.

The transport is a parameter, not an import. That is the whole reason the
`Caller` protocol exists: production supplies `urllib`, the tests supply
httpx over ASGI, and the walk itself — which is the part with the rules in
it — gets to be tested against a live PostgreSQL and MinIO in CI rather than
only run by hand.

Why HTTP at all, when this process could open the database: the library half
would indeed be cheap that way, but the match half goes through
`MatchManager`, `MatchHub`, `UnitOfWork` and `Materialiser`, and building
those here would duplicate the whole of `api/app.py`'s lifespan. A duplicate
service graph that drifts from the original at the first edit costs more
than any amount of convenience it buys — and going over HTTP means the demo
proves the operator's path works, not just that the tables can be filled.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from budge.demo.pictures import PALETTE, solid_png

# Demo category titles. The prefix is common and recognisable: §G.6 makes
# the walk idempotent by title, and the operator should be able to see
# which entries in the library came from the demo.
PREFIX = "Демо"

COLOURS = ("#e4572e", "#2e86e4", "#3fb950", "#d4a017", "#a371f7", "#e45ea0")


class Caller(Protocol):
    """One HTTP call, and what came back.

    Returns the status and the decoded body rather than raising: a 409 from
    a command route is an ordinary answer carrying a `reason` (§6.3), and a
    transport that raised on it would turn the domain's vocabulary into
    exceptions.
    """

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]: ...


@dataclass(frozen=True, slots=True)
class DemoPlan:
    board: tuple[int, int]
    players: int
    images: int
    start: bool


@dataclass(frozen=True, slots=True)
class DemoReport:
    match_id: str
    stage_token: str
    categories_created: int
    images_created: int
    started: bool


class DemoFailed(RuntimeError):
    """A call the demo cannot continue past."""


async def _expect(
    caller: Caller,
    method: str,
    path: str,
    *,
    json: object | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    accept: tuple[int, ...] = (200, 201),
) -> Any:
    status, payload = await caller.call(
        method, path, json=json, body=body, content_type=content_type
    )
    if status not in accept:
        raise DemoFailed(f"{method} {path} answered {status}: {payload}")
    # Commands answer with an outcome envelope, and "accepted" is not the
    # same thing as "200". A refusal here means going further is pointless.
    if isinstance(payload, dict) and payload.get("outcome") in {"rejected", "failed"}:
        raise DemoFailed(f"{method} {path} was refused: {payload.get('reason')}")
    return payload


async def _ensure_categories(
    caller: Caller, plan: DemoPlan
) -> tuple[list[str], list[str], int, int]:
    """§G.5 and §G.6: create the categories that are missing and stock them with pictures.

    Returns the ordinary and secret category ids plus the counts of what
    was actually created, so the report can honestly say "the second run
    did nothing".
    """
    width, height = plan.board
    cells = width * height
    wanted_ordinary = [f"{PREFIX}: тема {index + 1}" for index in range(cells)]
    wanted_secret = [f"{PREFIX}: секрет {index + 1}" for index in range(plan.players + 1)]

    existing = {
        str(row["title"]): row
        for row in await _expect(caller, "GET", "/api/library/categories")
    }

    ordinary: list[str] = []
    secrets: list[str] = []
    categories_created = 0
    images_created = 0

    for title, is_secret in [(t, False) for t in wanted_ordinary] + [
        (t, True) for t in wanted_secret
    ]:
        found = existing.get(title)
        if found is not None:
            # §G.6: the category already exists — its pictures are not
            # topped up, or every run would grow the deck.
            (secrets if is_secret else ordinary).append(str(found["id"]))
            continue
        created = await _expect(
            caller,
            "POST",
            "/api/library/categories",
            json={"title": title, "is_secret": is_secret},
        )
        category_id = str(created["id"])
        categories_created += 1
        (secrets if is_secret else ordinary).append(category_id)
        # §G.4: capped at the palette's own length — beyond it, the modulo
        # below would wrap around *within this one category* and repeat a
        # picture, which is exactly the deck §G.4 exists to rule out.
        image_count = min(plan.images, len(PALETTE))
        for index in range(image_count):
            name, rgb = PALETTE[(len(ordinary) + len(secrets) + index) % len(PALETTE)]
            stored = await _expect(
                caller,
                "POST",
                "/api/media",
                body=solid_png(rgb),
                content_type="image/png",
            )
            await _expect(
                caller,
                "POST",
                f"/api/library/categories/{category_id}/images",
                json={"media_sha256": str(stored["media_sha256"]), "answer_text": name},
            )
            images_created += 1

    return ordinary, secrets, categories_created, images_created


async def run(caller: Caller, plan: DemoPlan) -> DemoReport:
    """An empty system in, a match with a dealt board out."""
    _, secrets, categories_created, images_created = await _ensure_categories(caller, plan)

    width, height = plan.board
    created = await _expect(
        caller,
        "POST",
        "/api/matches",
        json={"board": {"width": width, "height": height}, "player_count": plan.players},
    )
    match_id = str(created["match_id"])

    for index in range(plan.players):
        player_id = str(uuid4())
        await _expect(
            caller,
            "POST",
            f"/api/matches/{match_id}/players",
            json={
                "player_id": player_id,
                "name": f"Игрок {index + 1}",
                "colour": COLOURS[index % len(COLOURS)],
            },
        )
        # A secret of their own for each player: `AssignSecret` refuses a
        # repeat with `duplicate_category`, and one category shared by
        # everyone would sink `deal`.
        await _expect(
            caller,
            "POST",
            f"/api/matches/{match_id}/secrets",
            json={"player_id": player_id, "category": secrets[index]},
        )

    await _expect(caller, "POST", f"/api/matches/{match_id}/deal")
    if plan.start:
        await _expect(caller, "POST", f"/api/matches/{match_id}/start")

    return DemoReport(
        match_id=match_id,
        stage_token=str(created["stage_token"]),
        categories_created=categories_created,
        images_created=images_created,
        started=plan.start,
    )
