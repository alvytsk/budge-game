"""§G: the demo reaches a playable match, doing so through the real routes.

The transport substituted here is fake — what's checked is the order and
composition of the calls, not the network. That this same walk works
against a live system is proved by the integration test in
`tests/api/test_demo_seed.py`.
"""

from typing import Any

import pytest

from budge.demo.seed import DemoPlan, run


class FakeApi:
    """The routes the demo touches, answering the way the real ones do."""

    def __init__(self) -> None:
        self.categories: list[dict[str, Any]] = []
        self.media: set[str] = set()
        self.images = 0
        self.secrets_assigned: list[str] = []
        self.dealt = False
        self.started = False

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, object]:
        if method == "GET" and path == "/api/library/categories":
            return 200, list(self.categories)
        if method == "POST" and path == "/api/media":
            assert body is not None
            digest = f"{len(self.media):064x}"
            self.media.add(digest)
            return 201, {"media_sha256": digest, "content_type": "image/png", "bytes": len(body)}
        if method == "POST" and path == "/api/library/categories":
            assert isinstance(json, dict)
            row = {
                "id": f"cat-{len(self.categories)}",
                "title": json["title"],
                "is_secret": json["is_secret"],
                "is_active": True,
                "version": 1,
                "active_image_count": 0,
            }
            self.categories.append(row)
            return 201, row
        if method == "POST" and path.endswith("/images"):
            self.images += 1
            return 201, {"id": f"img-{self.images}"}
        if method == "POST" and path == "/api/matches":
            return 201, {"outcome": "accepted", "match_id": "m-1", "stage_token": "tok"}
        if method == "POST" and path.endswith("/players"):
            return 200, {"outcome": "accepted"}
        if method == "POST" and path.endswith("/secrets"):
            assert isinstance(json, dict)
            self.secrets_assigned.append(str(json["category"]))
            return 200, {"outcome": "accepted"}
        if method == "POST" and path.endswith("/deal"):
            self.dealt = True
            return 200, {"outcome": "accepted"}
        if method == "POST" and path.endswith("/start"):
            self.started = True
            return 200, {"outcome": "accepted"}
        raise AssertionError(f"unexpected {method} {path}")


async def test_the_demo_reaches_a_dealt_match() -> None:
    api = FakeApi()
    report = await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert api.dealt is True
    assert api.started is False
    assert report.match_id == "m-1"
    assert report.stage_token == "tok"


async def test_start_is_pressed_only_when_asked() -> None:
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=True))
    assert api.started is True


async def test_every_player_gets_a_secret_of_their_own() -> None:
    """§2.3 and `AssignSecret`: two identical categories mean `duplicate_category`.

    Kills on: a demo that assigns one secret to everyone — `deal` would
    refuse, and the demo would fail on the second-to-last step.
    """
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert len(api.secrets_assigned) == 3
    assert len(set(api.secrets_assigned)) == 3


async def test_the_board_gets_enough_ordinary_categories() -> None:
    """§G.5: a 4x3 board for three players needs 9 ordinary categories, and the
    demo creates 12 — one per cell, not the bare minimum. Likewise 4 secret
    categories, not the bare `players` count.

    §G.5 is explicit the surplus is deliberate: "Запас в обеих строках
    намеренный: он оставляет место перераздаче и четвёртому игроку". Pinning
    only `>= cells - players` and `>= players` would let that headroom be
    quietly deleted — e.g. dropping `plan.players + 1` to `plan.players` in
    `_ensure_categories` still satisfies `>= 3` for three secrets. Pinning the
    exact counts documented in §G.5 makes such a change a deliberate edit to
    this test, not something it shrugs at.

    Kills on: `cells` computed as `width + height` instead of `width *
    height` (7 ordinary instead of 12), or `plan.players + 1` reduced to
    `plan.players` (3 secrets instead of 4).
    """
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    ordinary = [row for row in api.categories if not row["is_secret"]]
    secrets = [row for row in api.categories if row["is_secret"]]
    assert len(ordinary) == 12
    assert len(secrets) == 4


async def test_no_category_is_left_without_pictures() -> None:
    """§8 calls a category running out of images mid-duel a content defect,
    and a demo that produced such a defect would be demonstrating the
    wrong mechanic.

    Kills on: pictures added only to ordinary categories — the first attack
    on a secret would fail with `content_unavailable` in front of the room.
    """
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert api.images == len(api.categories) * 3


async def test_a_second_run_does_not_duplicate_the_library() -> None:
    """§G.6: idempotent by category title."""
    api = FakeApi()
    await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    after_first = len(api.categories)
    images_after_first = api.images
    second = await run(api, DemoPlan(board=(4, 3), players=3, images=3, start=False))
    assert len(api.categories) == after_first
    assert api.images == images_after_first
    assert second.categories_created == 0
    assert second.images_created == 0


async def test_a_refused_command_stops_the_demo_loudly() -> None:
    """A demo that silently continues past a refusal leaves the operator
    with a match in an unclear state and no message."""

    class RefusingApi(FakeApi):
        async def call(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
            if path.endswith("/deal"):
                return 409, {"outcome": "rejected", "reason": "secret_missing"}
            return await super().call(method, path, **kwargs)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="secret_missing"):
        await run(RefusingApi(), DemoPlan(board=(4, 3), players=3, images=3, start=False))


async def test_a_refusal_wrapped_in_200_stops_the_demo_too() -> None:
    """`_expect` inspects the outcome envelope, not only the HTTP status,
    because a command route can in principle answer 200 with a rejected
    outcome (§6.3) even though today's `api/outcomes.py` maps `Rejected` to
    409 and never to 200 — the previous test alone cannot reach this branch,
    since its 409 is already outside `accept` and short-circuits before the
    envelope is ever inspected. This test exists so that branch stays
    covered instead of looking like dead code to a future reader who checks
    coverage and deletes it.

    Kills on: `_expect` dropping the `payload.get("outcome") in
    {"rejected", "failed"}` check and trusting the status code alone.
    """

    class SoftRefusingApi(FakeApi):
        async def call(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
            if path.endswith("/secrets"):
                return 200, {"outcome": "rejected", "reason": "duplicate_category"}
            return await super().call(method, path, **kwargs)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="duplicate_category"):
        await run(SoftRefusingApi(), DemoPlan(board=(4, 3), players=3, images=3, start=False))
