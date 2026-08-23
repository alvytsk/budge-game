"""§8's admin surface, over the real HTTP transport."""

from typing import Any
from uuid import uuid4

import pytest

from api.conftest import TEST_PASSWORD, running_app
from podvinsya.api.app import build_app
from podvinsya.api.settings import ApiSettings
from support.media import InMemoryMediaStore

pytestmark = pytest.mark.integration

# Real pictures, because ruling 4 means the library will only name a digest
# the object store actually holds. Two distinct PNGs, so they hash apart.
PNG = b"\x89PNG\r\n\x1a\x0a" + bytes(range(64))
ANOTHER_PNG = b"\x89PNG\r\n\x1a\x0a" + bytes(range(64, 128))

# Well-formed but never uploaded: what ruling 4 refuses.
UNSTORED_DIGEST = "a" * 64


async def log_in(client: Any) -> None:
    assert (await client.post("/api/session", json={"password": TEST_PASSWORD})).status_code == 204


async def upload(client: Any, data: bytes) -> str:
    """Put a picture in the store and return the address it hashed to."""
    response = await client.post("/api/media", content=data)
    assert response.status_code == 201, response.text
    digest: str = response.json()["media_sha256"]
    return digest


async def create_category(client: Any, title: str = "История", *, secret: bool = False) -> str:
    response = await client.post(
        "/api/library/categories", json={"title": title, "is_secret": secret}
    )
    assert response.status_code == 201, response.text
    created: str = response.json()["id"]
    return created


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/library/categories", None),
        ("POST", "/api/library/categories", {"title": "x"}),
        ("GET", "/api/library/categories/{id}", None),
        ("PUT", "/api/library/categories/{id}", {"title": "x", "is_secret": False}),
        ("PUT", "/api/library/categories/{id}/active", {"is_active": False}),
        (
            "POST",
            "/api/library/categories/{id}/images",
            {"media_sha256": UNSTORED_DIGEST, "answer_text": "x"},
        ),
        (
            "PUT",
            "/api/library/images/{id}",
            {"media_sha256": UNSTORED_DIGEST, "answer_text": "x"},
        ),
        ("PUT", "/api/library/images/{id}/active", {"is_active": False}),
        ("PUT", "/api/library/categories/{id}/images/order", {"image_ids": []}),
        ("GET", "/api/library/readiness", None),
    ],
)
async def test_every_library_route_refuses_an_unauthenticated_caller(
    api_settings: ApiSettings, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Parametrized over all ten, for the reason plan 4's equivalent gives:
    forgetting the dependency on exactly one route is the failure a
    per-route test set is least likely to catch, because the test for the
    forgotten route is the one nobody wrote."""
    async with running_app(build_app(api_settings)) as client:
        response = await client.request(method, path.format(id=uuid4()), json=body)

    assert response.status_code == 401, f"{method} {path} let an anonymous caller through"


async def test_a_category_can_be_created_listed_and_edited(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)

        listed = await client.get("/api/library/categories")
        edited = await client.put(
            f"/api/library/categories/{category_id}",
            json={"title": "История СССР", "is_secret": False},
        )

    assert [row["title"] for row in listed.json()] == ["История"]
    assert edited.json()["category"]["title"] == "История СССР"


async def test_editing_a_category_bumps_the_version_over_the_wire(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """§5.3's invariant, visible to the operator.

    Kills on: an edit route that updated the row directly — the version
    would stay at 1, and a selection holding `FOR SHARE` would be protected
    by nothing."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)

        before = (await client.get(f"/api/library/categories/{category_id}")).json()
        await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, PNG), "answer_text": "Гагарин"},
        )
        after = (await client.get(f"/api/library/categories/{category_id}")).json()

    assert before["category"]["version"] == 1
    assert after["category"]["version"] == 2


async def test_deactivating_keeps_a_category_in_the_list(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """§5.3's «мягко»: «тему "аниме" ведущая не удалила, а выключила».

    Kills on: filtering the admin listing by `is_active` — the operator's
    only way back would be a database client."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client, "Аниме")

        await client.put(
            f"/api/library/categories/{category_id}/active", json={"is_active": False}
        )
        listed = (await client.get("/api/library/categories")).json()

    assert [(row["title"], row["is_active"]) for row in listed] == [("Аниме", False)]


async def test_an_image_digest_that_is_not_a_sha256_is_refused_by_the_api(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Ruling 8. Kills on: dropping the pattern, which pushes the refusal
    down to the check constraint and turns a 422 the operator can read into
    a 500 they cannot."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)

        response = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": "not-a-digest", "answer_text": "Гагарин"},
        )

    assert response.status_code == 422


async def test_an_uppercase_digest_is_refused_by_the_api(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Content addressing needs one object to have one name.

    Kills on: a case-insensitive pattern, which lets the same bytes be
    referenced under two digests."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)
        response = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": "A" * 64, "answer_text": "Гагарин"},
        )

    assert response.status_code == 422


async def test_the_listing_carries_the_active_image_count(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)
        first = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, PNG), "answer_text": "Гагарин"},
        )
        await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, ANOTHER_PNG), "answer_text": "Титов"},
        )
        await client.put(
            f"/api/library/images/{first.json()['id']}/active", json={"is_active": False}
        )

        listed = (await client.get("/api/library/categories")).json()

    assert listed[0]["active_image_count"] == 1


async def test_images_are_appended_and_can_be_reordered(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)
        ids = []
        for index in range(3):
            created = await client.post(
                f"/api/library/categories/{category_id}/images",
                json={
                    "media_sha256": await upload(client, PNG + bytes([index])),
                    "answer_text": f"a{index}",
                },
            )
            ids.append(created.json()["id"])

        reordered = await client.put(
            f"/api/library/categories/{category_id}/images/order",
            json={"image_ids": [ids[2], ids[0], ids[1]]},
        )

    assert [image["id"] for image in reordered.json()["images"]] == [ids[2], ids[0], ids[1]]
    assert [image["position"] for image in reordered.json()["images"]] == [0, 1, 2]


async def test_a_partial_reorder_is_refused(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Kills on: renumbering only the named subset, which silently reorders
    images the operator did not mention and can leave two sharing a
    position."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)
        first = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, PNG), "answer_text": "a"},
        )
        await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, ANOTHER_PNG), "answer_text": "b"},
        )

        response = await client.put(
            f"/api/library/categories/{category_id}/images/order",
            json={"image_ids": [first.json()["id"]]},
        )

    assert response.status_code == 409


async def test_readiness_names_the_thin_categories(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """§8's «мягкое предупреждение».

    Kills on: making it a hard refusal — §8 warns the operator, it does not
    block them, and the show is theirs to start."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)
        await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, PNG), "answer_text": "Гагарин"},
        )

        readiness = (await client.get("/api/library/readiness?cells=12")).json()

    assert readiness["threshold"] == api_settings.thin_image_threshold
    assert [row["id"] for row in readiness["thin"]] == [category_id]
    assert readiness["thin"][0]["active_image_count"] == 1
    assert readiness["ready"] is False


async def test_readiness_is_ready_when_the_library_can_fill_the_board(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        for index in range(4):
            await create_category(client, f"Тема {index}")

        readiness = (await client.get("/api/library/readiness?cells=4")).json()

    assert readiness["ordinary_available"] == 4
    assert readiness["ready"] is True


async def test_readiness_counts_secrets_apart_from_ordinary_categories(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """§3.4 places one secret per player and draws the rest from the
    ordinary pool.

    Kills on: counting secrets towards `ordinary_available`, which would
    report a library of nothing but secrets as ready to deal a board."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        await create_category(client, "Тема", secret=False)
        await create_category(client, "Секрет", secret=True)

        readiness = (await client.get("/api/library/readiness?cells=2")).json()

    assert readiness["ordinary_available"] == 1
    assert readiness["secrets_available"] == 1
    assert readiness["ready"] is False


async def test_readiness_ignores_inactive_categories(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Selection draws «из активных» (§8), so readiness must count the same
    set selection will.

    Kills on: counting every row — readiness would say yes and the deal
    would then refuse with `content_unavailable`."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        first = await create_category(client, "Тема 0")
        await create_category(client, "Тема 1")
        await client.put(f"/api/library/categories/{first}/active", json={"is_active": False})

        readiness = (await client.get("/api/library/readiness?cells=2")).json()

    assert readiness["ordinary_available"] == 1
    assert readiness["ready"] is False


async def test_no_library_route_deletes_anything(api_settings: ApiSettings) -> None:
    """§5.3's «мягко», at the routing layer.

    Kills on: adding a DELETE for convenience — the operator's «выключить,
    не удалять» would become irreversible, and a played match's log would
    point at rows that no longer exist."""
    paths = build_app(api_settings).openapi()["paths"]
    library_paths = {path: spec for path, spec in paths.items() if path.startswith("/api/library")}

    assert library_paths, "the library router is not mounted"
    offenders = [path for path, spec in library_paths.items() if "delete" in spec]
    assert not offenders


async def test_an_unknown_category_is_a_404(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Kills on: letting `UnknownCategory` escape, which turns a mistyped
    id into a 500 with a stack trace."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        assert (await client.get(f"/api/library/categories/{uuid4()}")).status_code == 404
        digest = await upload(client, PNG)
        assert (
            await client.put(
                f"/api/library/images/{uuid4()}",
                json={"media_sha256": digest, "answer_text": "x"},
            )
        ).status_code == 404


async def test_a_body_that_names_a_sender_is_refused(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """§7.4 on the admin surface too."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        response = await client.post(
            "/api/library/categories",
            json={"title": "История", "is_secret": False, "actor": str(uuid4())},
        )

    assert response.status_code == 422


async def test_an_image_cannot_name_a_digest_the_store_does_not_hold(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Ruling 4. §5.3 makes the log's link one-way and permanent —
    `AttackDeclared` writes image identifiers, and those rows are read for
    the rest of the match.

    Kills on: dropping the check. The failure moves from a 409 an operator
    sees at setup to a picture that fails to render in front of the room,
    discovered mid-duel."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)

        response = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": UNSTORED_DIGEST, "answer_text": "Гагарин"},
        )

    assert response.status_code == 409


async def test_an_image_can_name_a_digest_that_was_uploaded(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """The control for the test above: a check that refused everything
    would pass it and break the product."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)

        response = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, PNG), "answer_text": "Гагарин"},
        )

    assert response.status_code == 201


async def test_editing_an_image_checks_the_new_digest_too(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Kills on: checking only on create, which leaves the edit path as the
    way in — and an operator correcting a picture is exactly when a digest
    gets retyped."""
    async with running_app(build_app(api_settings)) as client:
        await log_in(client)
        category_id = await create_category(client)
        created = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": await upload(client, PNG), "answer_text": "Гагарин"},
        )

        response = await client.put(
            f"/api/library/images/{created.json()['id']}",
            json={"media_sha256": UNSTORED_DIGEST, "answer_text": "Титов"},
        )

    assert response.status_code == 409


async def test_an_unreachable_store_does_not_let_an_unchecked_digest_through(
    clean_db: None, clean_bucket: None, api_settings: ApiSettings
) -> None:
    """Kills on: treating `MediaUnavailable` as "not found" *or* as "fine".

    The first refuses valid work during an outage with a message about the
    wrong thing — the operator would go hunting for a digest that is
    correct. The second writes exactly the row this check exists to
    prevent, and does it precisely when nobody can verify anything."""
    app = build_app(api_settings)
    async with running_app(app) as client:
        await log_in(client)
        category_id = await create_category(client)
        digest = await upload(client, PNG)

        app.state.media = InMemoryMediaStore(fail=True)
        response = await client.post(
            f"/api/library/categories/{category_id}/images",
            json={"media_sha256": digest, "answer_text": "Гагарин"},
        )

        detail = (await client.get(f"/api/library/categories/{category_id}")).json()

    assert response.status_code == 503
    assert detail["images"] == []
