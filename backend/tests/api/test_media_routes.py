"""Upload and serve, over the real transport.

The store is the in-memory fake, not MinIO: what these tests are about is
the HTTP contract — who may call, what is refused, which headers go out —
and `tests/media/test_s3.py` is where the store itself is exercised against
a real S3.
"""

from typing import Any

import pytest
from fastapi import FastAPI

from api.conftest import TEST_PASSWORD, running_app
from podvinsya.api.app import build_app
from podvinsya.api.settings import ApiSettings
from podvinsya.media.digest import digest_of
from support.media import InMemoryMediaStore
from support.walk import every_string

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(128))
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><html><script>alert(1)</script></html>"

ABSENT = digest_of(b"never uploaded")


def with_fake_store(app: FastAPI, store: InMemoryMediaStore) -> None:
    """Swap the S3 store the lifespan built for the fake.

    Done after the lifespan rather than by not building one: `build_app`
    constructing the real store is itself part of what the wiring tests
    assert, and a test that bypassed the lifespan would stop exercising it.
    """
    app.state.media = store


async def log_in(client: Any) -> None:
    assert (
        await client.post("/api/session", json={"password": TEST_PASSWORD})
    ).status_code == 204


async def test_uploading_returns_the_digest_of_what_was_sent(
    api_settings: ApiSettings,
) -> None:
    """§7.6, and ruling 2: the server computes the address.

    Kills on: accepting a digest from the body — a client-chosen name lets
    two different pictures claim one address, which is the single property
    content addressing exists to give."""
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    async with running_app(app) as client:
        with_fake_store(app, store)
        await log_in(client)
        response = await client.post("/api/media", content=PNG)

    assert response.status_code == 201
    body = response.json()
    assert body["media_sha256"] == digest_of(PNG)
    assert body["content_type"] == "image/png"
    assert body["bytes"] == len(PNG)


async def test_uploading_twice_returns_one_digest(api_settings: ApiSettings) -> None:
    """Ruling 3, over the wire: an operator who uploads the same picture
    twice gets the same address and one object."""
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    async with running_app(app) as client:
        with_fake_store(app, store)
        await log_in(client)
        first = await client.post("/api/media", content=PNG)
        second = await client.post("/api/media", content=PNG)

    assert first.json()["media_sha256"] == second.json()["media_sha256"]
    assert store.puts == 1


async def test_what_was_uploaded_can_be_fetched_back_byte_for_byte(
    api_settings: ApiSettings,
) -> None:
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    async with running_app(app) as client:
        with_fake_store(app, store)
        await log_in(client)
        digest = (await client.post("/api/media", content=JPEG)).json()["media_sha256"]

        fetched = await client.get(f"/api/media/{digest}")

    assert fetched.status_code == 200
    assert fetched.content == JPEG
    assert fetched.headers["content-type"] == "image/jpeg"


async def test_uploading_requires_the_operator(api_settings: ApiSettings) -> None:
    """§7.4 — and the asymmetry with the GET below is the whole of ruling
    5, so both halves are asserted."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        response = await client.post("/api/media", content=PNG)

    assert response.status_code == 401


async def test_fetching_does_not_require_anyone(api_settings: ApiSettings) -> None:
    """Ruling 5, stated as a test so the exception stays deliberate.

    Kills on: adding `Depends(require_host)` to the GET — the stage screen
    holds a token in its URL and no cookie, and an `<img src>` carries no
    bearer header, so every picture on the big screen would 401."""
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    store.objects[digest_of(PNG)] = PNG
    async with running_app(app) as client:
        with_fake_store(app, store)
        # No login at all.
        response = await client.get(f"/api/media/{digest_of(PNG)}")

    assert response.status_code == 200
    assert response.content == PNG


async def test_an_svg_upload_is_refused(api_settings: ApiSettings) -> None:
    """Ruling 6, by name.

    Kills on: accepting it. §9.3 puts both surfaces in one Vite
    application, so an SVG fetched by `<img>` from this endpoint is served
    from the console's own origin — and an SVG is a document that can carry
    script, which would then run there."""
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    async with running_app(app) as client:
        with_fake_store(app, store)
        await log_in(client)
        response = await client.post("/api/media", content=SVG)

    assert response.status_code == 415
    assert store.objects == {}


async def test_an_html_upload_is_refused(api_settings: ApiSettings) -> None:
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        await log_in(client)
        assert (await client.post("/api/media", content=HTML)).status_code == 415


async def test_a_declared_content_type_does_not_override_the_bytes(
    api_settings: ApiSettings,
) -> None:
    """Kills on: trusting the request's `Content-Type`. An SVG announced as
    `image/png` would be stored and then served — and the sniffed type is
    what makes the header a fact rather than a client's claim."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        await log_in(client)
        response = await client.post(
            "/api/media", content=SVG, headers={"content-type": "image/png"}
        )

    assert response.status_code == 415


async def test_an_upload_past_the_limit_is_refused(api_settings: ApiSettings) -> None:
    """Kills on: dropping the check — a mistaken upload of a video would be
    read wholly into memory and stored before anybody noticed."""
    settings = api_settings.model_copy(update={"max_upload_bytes": 64})
    app = build_app(settings)
    store = InMemoryMediaStore()
    async with running_app(app) as client:
        with_fake_store(app, store)
        await log_in(client)
        response = await client.post("/api/media", content=PNG)

    assert response.status_code == 413
    assert store.objects == {}


async def test_a_fetch_of_an_unknown_digest_is_a_404(api_settings: ApiSettings) -> None:
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        assert (await client.get(f"/api/media/{ABSENT}")).status_code == 404


async def test_a_malformed_digest_is_a_404_not_a_422(api_settings: ApiSettings) -> None:
    """A malformed address and an absent one are the same fact to a caller.

    Kills on: validating the shape with a path converter — answering 422
    for the wrong shape and 404 for the right one tells somebody guessing
    which of their guesses had the right shape."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        for bad in ("not-a-digest", "A" * 64, "abc", "0" * 63):
            assert (await client.get(f"/api/media/{bad}")).status_code == 404, bad


async def test_a_served_object_is_cached_immutably(api_settings: ApiSettings) -> None:
    """Ruling 8, and §9.1's preload leans on it.

    Kills on: dropping the header — a screen that reconnects mid-show
    refetches the whole pack, and a second duel on one category refetches
    it again."""
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    store.objects[digest_of(PNG)] = PNG
    async with running_app(app) as client:
        with_fake_store(app, store)
        response = await client.get(f"/api/media/{digest_of(PNG)}")

    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_a_served_object_carries_nosniff(api_settings: ApiSettings) -> None:
    """Ruling 6's second half: the type is sniffed and trustworthy, and
    this stops the browser deciding otherwise.

    Kills on: dropping the header — a browser that sniffed for itself could
    reach a different conclusion about the same bytes than this server
    did."""
    app = build_app(api_settings)
    store = InMemoryMediaStore()
    store.objects[digest_of(PNG)] = PNG
    async with running_app(app) as client:
        with_fake_store(app, store)
        response = await client.get(f"/api/media/{digest_of(PNG)}")

    assert response.headers["x-content-type-options"] == "nosniff"


async def test_an_unreachable_store_is_a_503_not_a_404(api_settings: ApiSettings) -> None:
    """Kills on: mapping `MediaUnavailable` to 404 — an outage would read
    to the operator as "that picture is missing", and they would go looking
    for it in the library."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore(fail=True))
        await log_in(client)

        assert (await client.get(f"/api/media/{ABSENT}")).status_code == 503
        assert (await client.post("/api/media", content=PNG)).status_code == 503


async def test_no_media_response_contains_a_url(api_settings: ApiSettings) -> None:
    """§7.6: «в сообщениях ездят идентификаторы, а не URL».

    Kills on: an upload response that helpfully returns the location — the
    client would start storing it, and the identifier would stop being the
    contract the moment the route moved."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        await log_in(client)
        body = (await client.post("/api/media", content=PNG)).json()

    for value in every_string(body):
        assert "http" not in value
        assert "/api/media" not in value


async def test_an_empty_upload_is_refused(api_settings: ApiSettings) -> None:
    """Kills on: a sniffer that indexes rather than slices — zero bytes
    would reach the operator as a 500 rather than a 415."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        with_fake_store(app, InMemoryMediaStore())
        await log_in(client)
        assert (await client.post("/api/media", content=b"")).status_code == 415
