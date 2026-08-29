# Медиа «Подвинься» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the pictures behind `images.media_sha256` — §7.6's «медиа контент-адресуемо по sha256», §10's «S3-совместимое хранилище медиа в compose», and the second half of §10's «Healthcheck проверяет доступность БД и хранилища», which plan 4 wrote a seam for and left open.

**Architecture:** One port and one implementation, the way every other capability in this codebase is done. `MediaStore` declares four methods in `services/ports.py`; `S3MediaStore` implements them with `boto3` run off the event loop; the tests drive an in-memory fake. Two routes: an operator uploads bytes and is handed back the digest they hashed to, and anybody fetches those bytes by digest. The digest is computed by the server, never accepted from a client — that is what makes the address *content* addressing rather than a name a client chose.

**Tech Stack:** Python 3.12, `boto3` behind `asyncio.to_thread`, MinIO in compose as the S3-compatible store, `hashlib.sha256` from the standard library.

**Spec:** `docs/superpowers/specs/2026-08-22-budge-design.md` — §7.6 fixes the addressing, §5.3 the `media` store and the one-way link to the log, §8 the operator's workflow, §10 the container and the health check, §9.1 what the stage screen does with a pack.

**Branch:** `feature/media`, cut from `feature/content` → `feature/contracts` → `feature/api` → `feature/runtime` → `feature/persistence` → `main`, none merged. Nothing under `domain/`, `runtime/` or `db/` is modified; `library/catalogue.py` is not modified either — the store check lives in the route, above it.

## Global Constraints

- Python `>=3.12`. `mypy --strict` clean over `src/budge` and `tests`; `ruff check` clean with `select = ["E4", "E7", "E9", "F", "E501"]` and `line-length = 100`.
- Dependency direction stays one-way. `media/` is a service-layer implementation alongside `db/`, `runtime/` and `library/`; nothing under `domain/` or `runtime/` imports it.
- «Медиа контент-адресуемо по sha256; в сообщениях ездят идентификаторы, а не URL.» (§7.6) No frame and no REST response in this plan contains a URL. Plan 5's `test_no_emitted_type_carries_a_url` already walks the generated TypeScript for that, and it keeps passing.
- «Связь с логом односторонняя.» (§5.3) Media is never deleted while an image row references it, and no object here is ever overwritten — a digest names one sequence of bytes, permanently.
- The event loop is never blocked. §6.1 forbids the command loop to wait on I/O, and a synchronous S3 call anywhere in this process would stall every WebSocket writer in it — including, during a show, the stage screen preloading a pack.
- «Healthcheck проверяет доступность БД и хранилища.» (§10) The second entry plan 4's `health` docstring promised.
- «Клиент никогда не передаёт, кто он.» (§7.4) Upload takes `HostPrincipal` from the session cookie. The upload body carries bytes and nothing else — not a filename, not a digest, not a content type.
- Code, identifiers and comments in English.
- **Every test states what would kill it.**

## Rulings made while writing this plan

1. **`boto3` behind `asyncio.to_thread`, not `aioboto3`.** `aioboto3` brings twenty packages against `boto3`'s seven, and `aiobotocore` pins `botocore` to an exact range, which turns every dependency bump into a compatibility puzzle. The property that actually matters is that the loop is never blocked, and `to_thread` delivers it without the pin. Media I/O is also low-frequency by nature: an operator loading a few hundred pictures between shows, and one pack fetched per duel. *Cost if wrong: a thread per concurrent object, bounded by the default executor, on a box serving one room.*
2. **The server computes the digest; a client never supplies one.** Plan 6's ruling 8 said this plan would take the upload over: it does. `POST /api/media` reads bytes, hashes them, and answers with the digest. A digest a client chose is a name, not an address — and two different pictures could then claim one address, which is the single property content addressing exists to provide. `AddImageBody` keeps its `media_sha256` field, because attaching an already-uploaded picture to a category is a separate step, but ruling 4 makes that field checkable rather than trusted. *Cost if wrong: an operator uploads a picture twice and gets the same digest back, which is the point.*
3. **Storing an object that already exists is a no-op that reports success.** Content addressing makes an upload idempotent by construction: the same bytes hash to the same key, and re-writing them would be writing identical bytes over themselves. So `put` checks first and returns the digest either way. *Cost if wrong: a duplicate upload costs one HEAD instead of one PUT, and the operator cannot tell.*
4. **An image row may only name a digest the store actually holds.** §5.3 makes the log's link to the library one-way and permanent — `AttackDeclared` writes image ids, and those rows are read for the rest of the match. A row pointing at bytes that were never uploaded is a picture that fails to render in front of the room, discovered mid-duel. So `POST …/images` and `PUT /images/{id}` check the store and refuse with 409 otherwise. The check lives in the route, not in `LibraryCatalogue`: §5.3 makes that class the one writer, and giving it a second dependency to reach an object store would widen the one thing this codebase keeps narrow. *Cost if wrong: an operator who uploads and attaches in one screen sees no difference; one who types a digest by hand is refused, which is the intent.*
5. **`GET /api/media/{digest}` requires no principal.** Every other route in this system takes one, so the exception is stated rather than left to be noticed. The stage screen holds a token in its URL path and no cookie (§7.5), and an `<img src>` cannot carry a bearer header — so requiring a principal would mean putting a token in every image URL, on a screen whose whole job is to render pictures. What secures the endpoint instead is the address: a sha256 is 256 bits, unguessable, and only ever learned from a frame the server decided to send you. §1.1 puts the whole deployment on an isolated network, and §7.5 states plainly that the authentication there exists «чтобы сервер знал, какую из двух проекций строить», not to withstand an adversary. Uploading still requires the operator. *Cost if wrong: somebody on the meeting-room LAN who already has a digest can fetch the picture it names — which is a picture the room is looking at.*
6. **Only raster image types are accepted, and SVG is refused by name.** §9.3 puts both surfaces in one Vite application, so anything this endpoint serves is served from the console's own origin. An SVG is a document that can carry script; served as `image/svg+xml` it would execute there. The type is sniffed from the leading bytes rather than taken from the request, because a content type a client supplied is a claim, not a fact. Every response also carries `X-Content-Type-Options: nosniff`. *Cost if wrong: an operator who wants a vector diagram on the big screen exports it as PNG.*
7. **The content type is sniffed at serve time, not stored.** One function, `sniff`, decides at upload whether the bytes are acceptable and at serve time what to call them — so the two answers cannot disagree, and no metadata has to be kept in step with the object. *Cost if wrong: serving reads the first bytes it was going to read anyway.*
8. **Responses are cached hard and forever.** A content address names one immutable sequence of bytes, so `Cache-Control: public, max-age=31536000, immutable` is not a heuristic — it is a statement of fact about that URL. §9.1 preloads a whole pack at declaration, and a second duel on the same category, or a reconnecting screen, must not refetch it. *Cost if wrong: none available; a digest whose bytes changed would be a broken hash.*
9. **MinIO in compose, not garage.** §10 asks for «S3-совместимое хранилище», not a particular one. MinIO's bucket can be created through the S3 API itself, from a fixture — so the same container definition works unchanged in `compose.test.yaml` and in a GitHub Actions `services:` block, with no init container and no CLI step. Garage needs a TOML config plus `layout assign`, `key new`, `bucket create` and `bucket allow` before it will answer, which is a multi-step bootstrap CI cannot express as a service. The application speaks plain S3, so garage remains a drop-in for anyone who prefers it. *Cost if wrong: swapping the container changes one compose file and the fixture that creates the bucket.*
10. **`media/` is its own package, not part of `library/`.** They are different subsystems that happen to meet at one column: the library is rows in PostgreSQL that the operator edits, and media is immutable bytes in an object store that nobody edits. §5.3 lists them as separate things for the same reason. *Cost if wrong: one more directory holding two files.*

## File Structure

```
backend/pyproject.toml                            modify  boto3, boto3-stubs[s3]
backend/compose.test.yaml                         modify  + minio
backend/src/budge/services/ports.py           modify  + MediaStore, MediaUnavailable
backend/src/budge/media/__init__.py           create
backend/src/budge/media/digest.py             create  digest_of, sniff, ACCEPTED_TYPES
backend/src/budge/media/s3.py                 create  S3MediaStore
backend/src/budge/api/schemas/media.py        create  UploadedMediaBody
backend/src/budge/api/routes/media.py         create  POST /api/media, GET /api/media/{d}
backend/src/budge/api/routes/library.py       modify  refuse a digest the store lacks
backend/src/budge/api/app.py                  modify  wire the store, second health probe
backend/src/budge/api/settings.py             modify  s3 settings, max_upload_bytes
backend/src/budge/contracts/schema.py         modify  + UploadedMediaBody
backend/tests/support/media.py                    create  InMemoryMediaStore
backend/tests/media/                              create  digest, sniffing, the S3 store
backend/tests/api/test_media_routes.py            create
frontend/src/shared/api/contracts.ts              regenerate
.github/workflows/ci.yml                          modify  + minio service
```

---

### Task 1: The address, and what may live at one

The pure half: how a digest is computed, what byte sequences are acceptable, and the port everything else is written against. No I/O, no HTTP, no container.

**Files:**
- Modify: `backend/src/budge/services/ports.py`
- Create: `backend/src/budge/media/__init__.py`
- Create: `backend/src/budge/media/digest.py`
- Create: `backend/tests/support/media.py`
- Test: `backend/tests/media/__init__.py`
- Test: `backend/tests/media/test_digest.py`

**Interfaces:**
- Produces: `digest_of(data: bytes) -> str`, `sniff(data: bytes) -> str | None`,
  `ACCEPTED_TYPES: Mapping[str, tuple[bytes, ...]]`, `MediaStore` (Protocol),
  `MediaUnavailable(Exception)`; `InMemoryMediaStore` in tests.

- [ ] **Step 1: `media/digest.py`**

```python
"""What a media address is, and what may live at one.

§7.6: «Медиа контент-адресуемо по sha256». The address is the content, so
it is computed here and nowhere else — a digest that arrived from a client
is a name it chose, and two different pictures could then claim one
address, which is the single property content addressing exists to give.

`sniff` decides both questions this module is asked: at upload, whether
these bytes are something this system will serve, and at serve time, what
to call them. One function for both, so the two answers cannot disagree
(ruling 7).
"""

import hashlib
from collections.abc import Mapping

# Raster formats only. §9.3 puts both surfaces in one Vite application, so
# anything served here is served from the console's own origin — and an SVG
# is a document that can carry script, which would then run there. Refused
# by not appearing in this table (ruling 6).
ACCEPTED_TYPES: Mapping[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}

# WebP and AVIF are RIFF/ISO-BMFF containers: the marker is not at offset
# zero, so they are matched separately rather than bent into the table.
_WEBP = (b"RIFF", b"WEBP")
_AVIF_BRANDS = (b"avif", b"avis")


def digest_of(data: bytes) -> str:
    """Lowercase hex sha256 — the same shape `images.media_sha256`'s check
    constraint enforces, and the same one plan 6's Pydantic pattern does."""
    return hashlib.sha256(data).hexdigest()


def sniff(data: bytes) -> str | None:
    """The content type these bytes actually are, or `None`.

    Sniffed rather than taken from the request: a content type a client
    supplied is a claim, and this endpoint serves what it stores back to
    the room's browser.
    """
    for content_type, markers in ACCEPTED_TYPES.items():
        if any(data.startswith(marker) for marker in markers):
            return content_type
    if data[:4] == _WEBP[0] and data[8:12] == _WEBP[1]:
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in _AVIF_BRANDS:
        return "image/avif"
    return None
```

- [ ] **Step 2: `MediaStore` in `services/ports.py`**

```python
class MediaUnavailable(Exception):
    """The object store could not be reached.

    Distinct from "this digest is not there", which is an ordinary `None`:
    a missing object is a 404 the operator can act on, and an unreachable
    store is a 503 that belongs in the health check.
    """


class MediaStore(Protocol):
    """§5.3's `media`, content-addressed by sha256 (§7.6).

    Every method is `async` and none of them may block the event loop —
    a synchronous object-store call anywhere in this process would stall
    every WebSocket writer in it, including the stage screen preloading a
    pack mid-show.

    There is no `delete`. §5.3 makes the log's link one-way and permanent:
    `AttackDeclared` writes image ids, and those rows are read for the rest
    of the match and for every later reading of it.
    """

    async def put(self, data: bytes) -> str: ...
    async def get(self, digest: str) -> bytes | None: ...
    async def exists(self, digest: str) -> bool: ...
    async def healthy(self) -> bool: ...
```

- [ ] **Step 3: `tests/support/media.py`**

`InMemoryMediaStore` holding a `dict[str, bytes]`, with a `fail` switch that
makes every method raise `MediaUnavailable` — the health check and the 503
paths need a store that is reachable in one test and not in the next.

- [ ] **Step 4: Tests**

```python
def test_the_digest_is_the_sha256_of_the_bytes() -> None:
    """Kills on: hashing anything but the bytes — a filename, a salt, a
    length prefix. The check constraint on `images.media_sha256` and the
    Pydantic pattern would both still pass, and the address would simply
    not be a content address any more."""
    assert digest_of(b"hello") == hashlib.sha256(b"hello").hexdigest()

def test_identical_bytes_have_one_address() -> None:
def test_different_bytes_have_different_addresses() -> None:
def test_a_digest_matches_the_shape_the_database_enforces() -> None:
    """64 lowercase hex characters — the same thing `ck_images_media_sha256_
    is_a_digest` and plan 6's Pydantic pattern require, asserted here so a
    change to the hash is caught before it reaches an INSERT."""

@pytest.mark.parametrize("content_type,sample", [...])
def test_every_accepted_type_is_sniffed(...) -> None:
def test_an_svg_is_not_an_accepted_type() -> None:
    """Ruling 6, by name. Kills on: adding `image/svg+xml` to the table —
    an SVG served from the console's own origin is a script that runs
    there, and §9.3 puts both surfaces on that origin."""
def test_html_is_not_an_accepted_type() -> None:
def test_empty_bytes_are_not_an_accepted_type() -> None:
def test_a_truncated_header_does_not_crash_the_sniffer() -> None:
    """One and two bytes. Kills on: indexing without a slice, which turns a
    malformed upload into a 500 instead of a 415."""
```

- [ ] **Step 5:** `pytest`, `mypy`, `ruff check`, commit
  `"Compute a media address, and decide what may live at one"`.

---

### Task 2: The store, against a real S3

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/compose.test.yaml`
- Modify: `backend/src/budge/api/settings.py`
- Create: `backend/src/budge/media/s3.py`
- Test: `backend/tests/media/conftest.py`
- Test: `backend/tests/media/test_s3.py`

**Interfaces:**
- Produces: `S3MediaStore(endpoint, access_key, secret_key, bucket, *, region)`,
  `S3Settings` fields on `ApiSettings`.

- [ ] **Step 1: Dependencies and the container**

`boto3>=1.35` in `[project] dependencies`; `boto3-stubs[s3]>=1.35` in dev, so
`mypy --strict` stays honest about the client. MinIO in
`compose.test.yaml` on port 9002 — clear of 9000, which a local MinIO or a
neighbouring project may already hold, the same reasoning the postgres
service's 5434 was chosen under.

- [ ] **Step 2: `media/s3.py`**

Every call goes through `asyncio.to_thread` (ruling 1). The client is built
once in `__init__` — `boto3.client` is thread-safe for the operations here —
and `put` checks `exists` first (ruling 3).

`get` returns `None` for `NoSuchKey` and raises `MediaUnavailable` for
anything else: a missing object and an unreachable store are different
answers, and collapsing them would put "the store is down" behind a 404.

- [ ] **Step 3: `tests/media/conftest.py`**

An `s3_store` fixture that creates the bucket through the S3 API itself
(ruling 9) and truncates it between tests, plus the same "fail rather than
skip" treatment `tests/db/conftest.py` gives an unreachable PostgreSQL:

```python
UNREACHABLE = (
    f"Cannot reach the test object store at {ENDPOINT}.\n"
    "Start it with:  docker compose -f backend/compose.test.yaml up -d\n"
    "These tests fail rather than skip: a silently skipped integration "
    "suite reports green while proving nothing."
)
```

- [ ] **Step 4: Tests** (integration)

```python
async def test_bytes_come_back_exactly_as_they_went_in(...)
async def test_the_digest_it_returns_is_the_digest_of_the_bytes(...)
async def test_storing_the_same_bytes_twice_is_one_object(...)
    """Ruling 3. Kills on: rewriting on every upload — harmless today and
    wrong the moment the store is versioned or billed by write."""
async def test_a_digest_nobody_stored_is_none_not_an_error(...)
    """Kills on: letting `NoSuchKey` escape — a mistyped digest would 500
    instead of 404, and the operator would read it as a broken server."""
async def test_an_unreachable_store_raises_rather_than_returning_none(...)
    """Built against a closed port. Kills on: catching every exception and
    returning `None`, which hides an outage behind a 404 and makes the
    health check the only place it could ever surface — except that it
    would report healthy too."""
async def test_health_is_true_against_the_live_store(...)
async def test_health_is_false_against_a_closed_port(...)
    """§10. Kills on: raising out of `healthy`, which turns a degraded node
    into a 500 instead of the signal a load balancer reads."""
async def test_no_call_blocks_the_event_loop(...)
    """The constraint §6.1 states for the command loop and this plan
    extends to the process. A sentinel task is scheduled, an upload is
    awaited, and the sentinel must have run by the time it returns.

    Kills on: calling boto3 directly instead of through `to_thread` — every
    WebSocket writer in the process would stall for the length of an S3
    round trip, which during a show is the stage screen going blank."""
```

- [ ] **Step 5:** `pytest`, `mypy`, `ruff check`, commit
  `"Store bytes at their own address, without stopping the loop"`.

---

### Task 3: Upload and serve

**Files:**
- Create: `backend/src/budge/api/schemas/media.py`
- Create: `backend/src/budge/api/routes/media.py`
- Modify: `backend/src/budge/api/app.py` (mount, wire the store)
- Modify: `backend/src/budge/contracts/schema.py`
- Test: `backend/tests/api/test_media_routes.py`
- Regenerate: `frontend/src/shared/api/contracts.ts`

- [ ] **Step 1: `POST /api/media`**

Takes `HostPrincipal` and a raw body. The body carries bytes and nothing
else — no filename, no declared type, no digest (§7.4, ruling 2). It
answers `201` with `UploadedMediaBody(media_sha256, content_type, bytes)`.

Refusals: `413` past `max_upload_bytes`, `415` for anything `sniff` does not
recognise.

- [ ] **Step 2: `GET /api/media/{digest}`**

No principal (ruling 5). Refuses a digest that is not 64 lowercase hex with
`404` rather than `422` — a malformed address and an absent one are the
same fact to a caller, and distinguishing them tells a prober which of
their guesses had the right shape.

Every response carries `Content-Type` from `sniff`,
`Cache-Control: public, max-age=31536000, immutable` (ruling 8) and
`X-Content-Type-Options: nosniff` (ruling 6). A store that is unreachable
is `503`, not `404`.

- [ ] **Step 3: Tests**

```python
async def test_uploading_returns_the_digest_of_what_was_sent(...)
async def test_uploading_twice_returns_one_digest(...)
async def test_what_was_uploaded_can_be_fetched_back_byte_for_byte(...)
async def test_uploading_requires_the_operator(...)
async def test_fetching_does_not_require_anyone(...)
    """Ruling 5, stated as a test so the exception is deliberate and
    visible. Kills on: adding `Depends(require_host)` to the GET — the
    stage screen holds a token in its URL and no cookie, so every picture
    on the big screen would 401."""
async def test_an_svg_upload_is_refused(...)
    """Ruling 6. Kills on: accepting it — §9.3 serves both surfaces from
    one origin, so an SVG fetched by `<img>` from that origin is script
    running there."""
async def test_an_html_upload_is_refused(...)
async def test_an_upload_past_the_limit_is_refused(...)
async def test_a_fetch_of_an_unknown_digest_is_a_404(...)
async def test_a_malformed_digest_is_a_404_not_a_422(...)
async def test_a_served_object_is_cached_immutably(...)
    """Ruling 8, and §9.1's preload depends on it. Kills on: dropping the
    header — a screen reconnecting mid-show refetches the whole pack."""
async def test_a_served_object_carries_nosniff(...)
async def test_an_unreachable_store_is_a_503_not_a_404(...)
    """Kills on: mapping `MediaUnavailable` to 404 — an outage would read
    to the operator as "that picture is missing", and they would go
    looking for it in the library."""
async def test_no_media_response_contains_a_url(...)
    """§7.6: «в сообщениях ездят идентификаторы, а не URL». Kills on: an
    upload response that helpfully returns the location — the client would
    start storing it, and the identifier would stop being the contract."""
```

- [ ] **Step 4:** Add `UploadedMediaBody` to `contracts/schema.py`, run
  `budge export-types`, commit the regenerated file.

- [ ] **Step 5:** `pytest`, `mypy`, `ruff check`, `budge export-types --check`,
  commit `"Take a picture in, and hand it back by its address"`.

---

### Task 4: The library only names pictures that exist

**Files:**
- Modify: `backend/src/budge/api/routes/library.py`
- Test: `backend/tests/api/test_library_routes.py` (extend)

- [ ] **Step 1: Check the store before attaching**

`POST /api/library/categories/{id}/images` and `PUT /api/library/images/{id}`
call `store.exists(body.media_sha256)` and refuse with `409` otherwise
(ruling 4). The check is in the route, above `LibraryCatalogue`, which stays
the one writer with one dependency.

- [ ] **Step 2: Tests**

```python
async def test_an_image_cannot_name_a_digest_the_store_does_not_hold(...)
    """§5.3 makes the link one-way and permanent: `AttackDeclared` writes
    image ids, and a row pointing at bytes nobody uploaded is a picture
    that fails to render in front of the room, discovered mid-duel.

    Kills on: dropping the check — the failure moves from a 409 at setup
    to a blank screen during a show."""
async def test_an_image_can_name_a_digest_that_was_uploaded(...)
async def test_editing_an_image_checks_the_new_digest_too(...)
    """Kills on: checking only on create, which leaves the edit path as
    the way in."""
async def test_an_unreachable_store_does_not_let_an_unchecked_digest_through(...)
    """Kills on: treating `MediaUnavailable` as "not found" *or* as
    "fine" — the first refuses valid work during an outage with a message
    about the wrong thing, the second writes exactly the row this check
    exists to prevent."""
```

- [ ] **Step 3:** `pytest`, `mypy`, `ruff check`, commit
  `"Refuse to name a picture nobody uploaded"`.

---

### Task 5: The second health probe, and the CI that runs against a real store

**Files:**
- Modify: `backend/src/budge/api/app.py`
- Modify: `.github/workflows/ci.yml`
- Test: `backend/tests/api/test_app.py` (extend)
- Test: `backend/tests/api/test_wiring.py` (extend)

- [ ] **Step 1: `health` gains `storage`**

Plan 4 wrote the seam and said so in the docstring: «§10 wants storage
checked too. Object storage arrives with plan 6's media; when it does, it
becomes a second entry in `checks` below.» Add the entry and delete the
paragraph — a comment describing a gap that has been closed is a comment
that lies.

- [ ] **Step 2: Tests**

```python
async def test_health_reports_both_checks(...)
    """§10: «Healthcheck проверяет доступность БД и хранилища.»"""
async def test_health_is_degraded_when_only_the_store_is_down(...)
    """The check that makes the second probe worth having. Kills on:
    reporting `ok` while storage is false — which is what an `all()` over a
    dict that never gained its second key does, silently."""
async def test_the_stale_promise_is_gone_from_the_docstring(...)
    """Kills on: leaving plan 4's «object storage arrives with plan 6»
    paragraph in place after it arrived. Reads `health.__doc__` and fails
    on the sentence."""
```

- [ ] **Step 3: CI**

MinIO as a second service in the `backend` job, with the same health-check
options the postgres service uses, and the endpoint passed the same way the
database URL is — through the default in `tests/support`, so no CI-only
variable can drift from what a developer runs locally.

- [ ] **Step 4: Run every workflow step locally, in order**

```bash
cd backend
.venv/bin/python -m ruff check
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/budge export-types --check
```

- [ ] **Step 5:** Commit `"Check the store the way §10 asks, and run CI against one"`.

- [ ] **Step 6:** Use superpowers:finishing-a-development-branch.
