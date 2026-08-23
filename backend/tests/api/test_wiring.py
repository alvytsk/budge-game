"""The composition root, and the two orderings inside it that matter.

Nothing here tests a route. What it tests is that the graph `build_app`
constructs is the one the rest of the plan assumed — the hub is the
manager's broadcaster, the shutdown order is manager-then-engine, and the
two leaves plan 6 has not written yet fail the way §6.3 and §8 say they
should rather than taking a match off the air.
"""

from typing import cast

import pytest

from api.conftest import TEST_PASSWORD, running_app
from podvinsya.api.app import build_app
from podvinsya.api.content import (
    CachingContentDirectory,
    UnavailableCategories,
    UnavailableContent,
)
from podvinsya.api.hub import MatchHub
from podvinsya.api.services import CommandGateway, MatchLifecycle, ReadOnlyMatches
from podvinsya.api.settings import ApiSettings
from podvinsya.domain.ids import CategoryId
from podvinsya.services.ports import ContentExhausted, Transaction
from uuid import uuid4

pytestmark = pytest.mark.integration

BOARD = {"width": 3, "height": 4}


async def test_the_lifespan_builds_the_whole_graph(api_settings: ApiSettings) -> None:
    """Kills on: leaving anything off `app.state` — every one of these is
    read by a route, and the failure without it is an `AttributeError`
    inside a request or, worse, inside a writer task."""
    app = build_app(api_settings)
    async with running_app(app):
        state = app.state
        assert isinstance(state.hub, MatchHub)
        assert isinstance(state.services.lifecycle, MatchLifecycle)
        assert isinstance(state.services.gateway, CommandGateway)
        assert isinstance(state.services.directory, CachingContentDirectory)
        assert isinstance(state.read_only, ReadOnlyMatches)
        assert state.clock is state.services.clock


async def test_the_hub_is_the_manager_s_broadcaster(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """The one connection that makes a frame reach a socket at all.

    Kills on: wiring a different broadcaster into the manager — every test
    of the hub and every test of the sockets would still pass, and no frame
    would ever be published in production."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        await client.post("/api/session", json={"password": TEST_PASSWORD})
        created = await client.post(
            "/api/matches", json={"board": BOARD, "player_count": 2}
        )
        match_id = created.json()["match_id"]

        from uuid import UUID

        from podvinsya.domain.ids import MatchId

        match = MatchId(UUID(match_id))
        with app.state.hub.subscribe(match) as subscriber:
            await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": str(uuid4()), "name": "A", "colour": "#111111"},
            )
            assert subscriber.pending() == 1
            update = await subscriber.next()

    assert update.base_seq == 1
    assert [type(event).__name__ for event in update.events] == ["PlayerAdded"]


async def test_shutdown_stops_the_manager_before_disposing_the_engine(
    clean_db: None, api_settings: ApiSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills on: reversing the two.

    `MatchManager.shutdown` resolves every origin still waiting on a
    command as it goes; doing that against a disposed pool would turn a
    clean stop into a quarantine for every match that happened to be
    mid-command — and the operator would come back to a match the server
    refuses to touch."""
    app = build_app(api_settings)
    order: list[str] = []

    async with running_app(app) as client:
        await client.post("/api/session", json={"password": TEST_PASSWORD})
        await client.post("/api/matches", json={"board": BOARD, "player_count": 2})

        manager = app.state.services.manager
        engine = app.state.engine
        original_shutdown = manager.shutdown
        # `AsyncEngine` has `__slots__`, so `dispose` is patched on the
        # class rather than on the instance; monkeypatch restores it.
        original_dispose = type(engine).dispose

        async def watched_shutdown() -> None:
            order.append("manager")
            await original_shutdown()

        async def watched_dispose(self: object, *args: object, **kwargs: object) -> None:
            order.append("engine")
            await original_dispose(self, *args, **kwargs)

        monkeypatch.setattr(manager, "shutdown", watched_shutdown)
        monkeypatch.setattr(type(engine), "dispose", watched_dispose)

    assert order == ["manager", "engine"]


async def test_dealing_with_no_library_is_a_rejection_not_a_quarantine(
    clean_db: None, api_settings: ApiSettings
) -> None:
    """§6.3 and §8: a content shortfall is «обычный отказ, не авария».

    Kills on: wiring a bank whose failure §6.3 quarantines on — the match
    would go off the air for a gap §8 calls an administrator's problem, and
    the operator would lose the "перераздать" button §9.2 puts on the setup
    screen."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        await client.post("/api/session", json={"password": TEST_PASSWORD})
        created = await client.post(
            "/api/matches", json={"board": BOARD, "player_count": 2}
        )
        match_id = created.json()["match_id"]
        for index in range(2):
            player = str(uuid4())
            await client.post(
                f"/api/matches/{match_id}/players",
                json={"player_id": player, "name": f"P{index}", "colour": "#111111"},
            )
            await client.post(
                f"/api/matches/{match_id}/secrets",
                json={"player_id": player, "category": str(uuid4())},
            )

        dealt = await client.post(f"/api/matches/{match_id}/deal")
        # The match must still answer afterwards — a quarantined one would
        # refuse everything with a 503 from here on.
        snapshot = await client.get(f"/api/matches/{match_id}")

    assert dealt.status_code == 409
    assert dealt.json()["reason"] == "content_unavailable"
    assert snapshot.status_code == 200


async def test_the_unwritten_leaves_are_named_rather_than_absent() -> None:
    """Plan 6 supplies both. Until then they are explicit implementations,
    not `None`.

    Kills on: wiring `None` for either, which turns "no library yet" into
    an `AttributeError` inside a writer task — a frame that never arrives,
    with a traceback in a log nobody is reading during a show."""
    # The transaction is never touched — the raise is the first statement —
    # so any object satisfies the signature here.
    tx = cast(Transaction, object())
    with pytest.raises(ContentExhausted):
        await UnavailableCategories().draw_categories(tx, 4, exclude=frozenset())
    with pytest.raises(ContentExhausted):
        await UnavailableCategories().draw_images(tx, CategoryId(uuid4()), 60)
    described = await UnavailableContent().describe(categories=frozenset(), images=frozenset())
    assert described.category_names == {}
