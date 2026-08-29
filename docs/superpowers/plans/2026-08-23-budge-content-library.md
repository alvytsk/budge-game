# Библиотека контента «Подвинься» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the server a content library — §5.3's two ordinary tables, the version bump that makes §5.3's locking invariant hold, and the two ports plan 3 and plan 4 declared and left unimplemented — so that a match can actually be dealt, a duel can actually draw a pack, and the stage screen and the console can actually be told what a category is called.

**Architecture:** `library/` is an ordinary CRUD subsystem, not an event-sourced one (§5.3: «Обычные таблицы, не event-sourced»). It owns two tables and exactly three seams onto the rest of the system: `LibraryCatalogue`, which is the only thing that writes and therefore the only place `categories.version` is bumped; `DatabaseCategoryBank`, which is plan 3's `CategoryBank` implemented with the `FOR SHARE` selection §5.3 requires; and `DatabaseContentDirectory`, which is plan 4's `ContentDirectory`. Both ports are already declared, already faked in tests, and already wired to explicitly-unavailable implementations — so this plan replaces two objects in `build_app` and nothing else changes shape.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, PostgreSQL, FastAPI — all already present. No new dependency.

**Spec:** `docs/superpowers/specs/2026-08-22-budge-design.md` — §5.3 is the schema and the locking invariant, §8 is the library's behaviour and the content-defect policy, §3.4 is what selection has to produce.

**Scope: media is a separate plan.** §13's step 6 reads «Контент и админка: библиотека, медиа, отбор на партию». Media is its own subsystem — content-addressed blob storage, an upload path, an S3-compatible container in compose, and §10's second health probe — and it produces working software on its own, as does this. `images.media_sha256` lands here as the plain column §5.3 specifies; the store behind it is the next plan's. Nothing here reads or writes a byte of media.

**Branch:** `feature/content`, cut from `feature/contracts` → `feature/api` → `feature/runtime` → `feature/persistence` → `main`, none merged. Nothing under `domain/` or `runtime/` is modified.

## Global Constraints

- Python `>=3.12`. `mypy --strict` clean over `src/budge` and `tests`; `ruff check` clean with `select = ["E4", "E7", "E9", "F", "E501"]` and `line-length = 100`.
- Dependency direction stays one-way: `api → services → domain`, with `library` a service-layer implementation alongside `db` and `runtime`. Nothing under `domain/` or `runtime/` imports `library`.
- «Контент удаляется только мягко, флагом `is_active`.» (§5.3) No route, repository method or migration in this plan issues a `DELETE` against `categories` or `images`. A test asserts the absence.
- «Каждая семантическая правка категории бампает `categories.version`… Бамп обеспечивается ровно в одном месте, и это покрыто тестом.» (§5.3) Every write goes through `LibraryCatalogue`, the bump lives in one private method, and a test walks the module to prove no other write path exists.
- «Связь с логом односторонняя… Поздняя правка библиотеки не может задним числом изменить уже сыгранную дуэль.» (§5.3) Nothing here reads `match_events`, and no foreign key points from `images` to a match.
- «Отбор на партию: из активных, без повторов, число равно числу клеток поля.» (§8)
- «Исчерпание категории посреди дуэли трактуется как дефект контента, а не игровая ситуация.» (§8) A shortfall reaches the operator as `ContentExhausted`, which §6.3 already routes as an ordinary rejection.
- «Клиент никогда не передаёт, кто он.» (§7.4) The admin routes take `HostPrincipal` from the session cookie like every other route, and no body names a caller. Plan 4's `test_no_inbound_model_names_an_actor` gains this plan's module.
- «Миграции применяются отдельным шагом до старта приложения.» (§10) One new Alembic revision, `0002`.
- Code, identifiers and comments in English. A category `title` and an image `answer_text` are user content and may be any language — they are data, not source.
- **Every test states what would kill it.**

## Rulings made while writing this plan

1. **`draw_images` returns up to `count`, and raises only when there are none.** `IMAGE_PACK_SIZE` is 60, and §8 expects 100+ images per category — but a category with 40 must still be playable, and §8 puts the error at «если пачка всё же кончилась **в дуэли**», not at the draw. So a short pack is legal, the domain's own `IMAGES_EXHAUSTED` fires only if a duel actually runs past the end, and `ContentExhausted` is reserved for a category with no active images at all — which is a category that cannot be played at all. *Cost if wrong: a duel that outlasts a thin category refuses mid-play instead of at declaration; §8 calls that exact case a content defect and expects the operator to see it.*
2. **The pack is drawn in random order, not in `position` order.** `images.position` is the operator's ordering for the admin screen; a duel that always played a category's images in the same order would make a repeat showing of that category predictable to anyone who saw the first. §3.5 already draws the order once and writes it whole into `AttackDeclared`. *Cost if wrong: two duels on one category in one evening could open with the same picture — visible to the room, and the whole reason the order is drawn rather than fixed.*
3. **`FOR SHARE` is taken on `categories`, in the caller's transaction, exactly as §5.3 says.** The bank's methods already take a `Transaction`, and plan 3's `CategoryBank` docstring already explains why: «§5.3 selects content under `FOR SHARE`, and those locks are released when the transaction ends — which is why §6.3 replays a retried attempt whole». So this is implementation, not design. *Cost if wrong: none — the seam was built for it.*
4. **The version bump is a database-side `version = version + 1`, not a read-modify-write.** Two operators editing one category is not a scenario this deployment has (§1.1, one operator), but a read-modify-write bump would be wrong for a reason that has nothing to do with concurrency: it needs the row to have been read first, and the edit paths that do not read it are exactly the ones §5.3 warns will «проскользнуть мимо блокировки». *Cost if wrong: none; the SQL is shorter too.*
5. **A "semantic edit" is defined by enumeration, in one place.** §5.3 does not define the term, so this plan does: changing a category's `title` or `is_secret`, or adding, editing, deactivating or reordering any of its images. Toggling the category's own `is_active` is *not* one — deactivation does not change what the category *is*, and §5.3's invariant exists to protect a selection that has already taken `FOR SHARE`, which an `is_active` flip cannot invalidate mid-transaction because selection filters on it at read time. The enumeration lives as one constant next to the bump. *Cost if wrong: an unnecessary bump is harmless; a missing one is the bug §5.3 names, so the enumeration errs towards bumping.*
6. **Deactivating a category does not deactivate its images.** They stay as they are, so re-activating a category restores exactly what it had — which is §5.3's «тему "аниме" ведущая не удалила, а выключила, потому что играли три выпуска подряд», a decision expected to be reversed. Selection filters on `categories.is_active` *and* `images.is_active`, so an inactive category is unreachable either way. *Cost if wrong: an operator who deactivates and reactivates gets their images back, which is what they expected.*
7. **The thinness warning is a field on the listing, not a blocking check.** §8 asks for «мягкое предупреждение», and the selection §3.4 performs is automatic — there is no screen on which an operator chooses categories one by one. So `GET /api/library/categories` carries `active_image_count`, and `GET /api/library/readiness` answers «can this library fill a board of N cells, and which categories are thin» in one call the setup screen can render. Neither refuses anything. *Cost if wrong: an operator can still deal a board from a thin library and find out during the show — which is what «мягкое» means.*
8. **`media_sha256` is `nullable=False` with no store behind it yet.** §5.3 declares the column, and an image row without one is an image nobody can show. The next plan supplies the bytes; until then the admin API takes the digest as a value, and a test asserts it is a 64-character lowercase hex string — the one property that is true of a sha256 whether or not anything has been stored under it. *Cost if wrong: the next plan adds an upload that produces the digest instead of accepting it, and this validation stays as the invariant it already is.*

## File Structure

```
backend/src/budge/db/models.py                    modify  + Category, Image
backend/src/budge/db/migrations/versions/0002_content_library.py  create
backend/src/budge/library/__init__.py             create
backend/src/budge/library/catalogue.py            create  LibraryCatalogue — the only writer
backend/src/budge/library/bank.py                 create  DatabaseCategoryBank
backend/src/budge/library/directory.py            create  DatabaseContentDirectory
backend/src/budge/api/schemas/library.py          create  admin bodies and responses
backend/src/budge/api/routes/library.py           create  the admin surface
backend/src/budge/api/app.py                      modify  wire the two real implementations
backend/src/budge/contracts/schema.py             modify  + the new roots
backend/tests/library/                                create  one module per seam
backend/tests/db/test_schema.py                       modify  + the library's constraints
backend/tests/api/test_commands.py                    modify  + the new inbound module
frontend/src/shared/api/contracts.ts                  regenerate
```

---

### Task 1: The two tables §5.3 writes out

**Files:**
- Modify: `backend/src/budge/db/models.py`
- Create: `backend/src/budge/db/migrations/versions/0002_content_library.py`
- Test: `backend/tests/db/test_schema.py` (extend)

**Interfaces:**
- Produces: `Category`, `Image` (ORM models); Alembic revision `0002`.

- [ ] **Step 1: Add the models**

Append to `db/models.py`:

```python
class Category(Base):
    """§5.3's `categories`, an ordinary table and deliberately not
    event-sourced: the library outlives every match, and a log of its edits
    would be a second history nobody replays.

    `version` is not bookkeeping. §5.3 makes it a locking invariant:
    selection takes `FOR SHARE` on this row, and an edit path that changed
    an image without touching this row would slip past that lock. The bump
    lives in exactly one place — `LibraryCatalogue._bump` — and
    `test_no_write_outside_the_catalogue` is what keeps it there.

    There is no delete. §5.3: «контент удаляется только мягко, флагом
    `is_active`» — the observed behaviour of an operator who turned a theme
    off after three editions rather than throwing it away.
    """

    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Image(Base):
    """§5.3's `images`. `answer_text` is the thing §7.1 forbids the stage
    screen to ever receive, which is why it lives here and reaches the
    frame layer only through `ContentDirectory` — a port whose stage-side
    caller passes `images=frozenset()` and therefore never asks.

    `media_sha256` is content-addressable by definition (§7.6: «медиа
    контент-адресуемо по sha256»); the store behind it arrives with the
    media plan. The check constraint below is the part that is true either
    way.
    """

    __tablename__ = "images"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    media_sha256: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        # A sha256 is 64 lowercase hex characters. Checked in the schema
        # rather than only in Pydantic because the media plan will write
        # here too, and a digest that is not one is a row pointing at
        # nothing that could ever exist.
        CheckConstraint(
            "media_sha256 ~ '^[0-9a-f]{64}$'", name="ck_images_media_sha256_is_a_digest"
        ),
        CheckConstraint("position >= 0", name="ck_images_position_non_negative"),
    )
```

- [ ] **Step 2: Write the migration**

`0002_content_library.py`, `down_revision = "0001"`, creating both tables with
the same constraints, and a `downgrade` that drops them.

- [ ] **Step 3: Check the migration against the models**

`alembic check` is already exercised by `tests/db/test_migrations.py`. Run
`pytest tests/db/test_migrations.py -q` and expect it to pass — a mismatch
between the model and the migration fails there, which is that test's whole
job.

- [ ] **Step 4: Extend `tests/db/test_schema.py`**

```python
async def test_an_image_digest_must_be_a_sha256(...) -> None:
    """Kills on: dropping the check constraint. The media plan writes here
    too, and a row whose digest is not one points at an object that cannot
    exist under any content-addressed scheme."""


async def test_a_category_starts_active_at_version_one(...) -> None:
    """Kills on: defaulting `version` to 0, which would make the first bump
    produce 1 and be indistinguishable from a category never edited."""


async def test_deleting_a_category_cascades_to_its_images(...) -> None:
    """The FK is `ON DELETE CASCADE` so a hard delete cannot orphan rows —
    but nothing in this system performs one (§5.3). Asserted so that if a
    later plan adds an administrative purge, it does not leave images
    behind."""
```

- [ ] **Step 5:** `pytest -q`, `mypy`, `ruff check`, commit
  `"Give the library the two tables §5.3 writes out"`.

---

### Task 2: The only writer, and the bump that has to be in one place

§5.3's invariant is the point of this task: «Бамп обеспечивается ровно в
одном месте, и это покрыто тестом.»

**Files:**
- Create: `backend/src/budge/library/__init__.py`
- Create: `backend/src/budge/library/catalogue.py`
- Test: `backend/tests/library/__init__.py`
- Test: `backend/tests/library/test_catalogue.py`

**Interfaces:**
- Produces: `LibraryCatalogue` with `create_category`, `rename_category`,
  `set_category_active`, `add_image`, `edit_image`, `set_image_active`,
  `reorder_images`, `list_categories`, `category_detail`; `CategoryRow`,
  `ImageRow`, `UnknownCategory`, `UnknownImage`.

- [ ] **Step 1: Write the failing tests**

The load-bearing one first:

```python
def test_no_write_outside_the_catalogue() -> None:
    """§5.3: «Бамп обеспечивается ровно в одном месте, и это покрыто
    тестом.» This is that test.

    It parses every module under `src/budge/` and fails on any
    `update(Category)`, `update(Image)`, `insert`, `delete` or
    `session.add` naming a library model outside `library/catalogue.py`.
    A second write path is exactly what §5.3 says will slip past the
    `FOR SHARE` lock: it would change what a category *is* without moving
    the row selection locked.

    Kills on: an admin route that updates a row directly because it is one
    line shorter than calling the catalogue."""
```

and the behavioural ones:

```python
async def test_renaming_a_category_bumps_its_version(...)
async def test_changing_is_secret_bumps_its_version(...)
async def test_adding_an_image_bumps_the_category_version(...)
async def test_editing_an_image_bumps_the_category_version(...)
async def test_deactivating_an_image_bumps_the_category_version(...)
async def test_reordering_images_bumps_the_category_version(...)
async def test_deactivating_the_category_itself_does_not_bump(...)
    """Ruling 5: deactivation does not change what the category *is*, and
    selection filters on `is_active` at read time — so a flip cannot
    invalidate a selection that has already locked the row.

    Kills on: bumping on every write indiscriminately, which would make the
    version meaningless as a signal and hide a genuinely missing bump."""
async def test_the_bump_is_a_database_side_increment(...)
    """Ruling 4. Kills on: read-modify-write, which needs the row to have
    been read — and the paths that do not read it are the ones §5.3 warns
    about."""
async def test_nothing_is_ever_hard_deleted(...)
    """§5.3's «мягко». Deactivating leaves the row and its images in place
    and readable.

    Kills on: implementing `set_category_active(False)` as a DELETE, which
    would make the operator's «выключить, не удалять» irreversible."""
async def test_deactivating_a_category_leaves_its_images_active(...)
    """Ruling 6: reactivation restores exactly what was there."""
async def test_an_unknown_category_is_reported_not_ignored(...)
    """Kills on: an UPDATE matching zero rows returning quietly, which
    turns a typo'd id in an admin request into a silent success."""
```

- [ ] **Step 2: Run them and watch them fail**

`pytest tests/library -q`. Expected: collection error, `budge.library`
does not exist.

- [ ] **Step 3: Write `library/catalogue.py`**

Every public method opens its own transaction, and every one that performs
a semantic edit calls `self._bump(session, category_id)` inside it. `_bump`
is:

```python
_SEMANTIC_EDITS = (
    "a category's title or is_secret flag; an image added, edited, "
    "deactivated or reordered"
)


async def _bump(self, session: AsyncSession, category_id: UUID) -> None:
    """§5.3's locking invariant, in the one place ruling 5 puts it.

    Database-side increment (ruling 4): a read-modify-write needs the row
    to have been read first, and the edit paths that do not read it are
    exactly the ones §5.3 says will slip past the `FOR SHARE` lock.

    What counts as a semantic edit is enumerated rather than inferred:
    {_SEMANTIC_EDITS}. Toggling the category's own `is_active` is not one
    — see the plan's ruling 5.
    """
    result = await session.execute(
        update(Category).where(Category.id == category_id).values(version=Category.version + 1)
    )
    if cast(CursorResult[Any], result).rowcount == 0:
        raise UnknownCategory(category_id)
```

- [ ] **Step 4:** Run the tests, then `mypy`, `ruff check`.

- [ ] **Step 5: Commit** `"Bump a category's version in exactly one place"`.

---

### Task 3: Selection — `CategoryBank`, at last implemented

**Files:**
- Create: `backend/src/budge/library/bank.py`
- Test: `backend/tests/library/test_bank.py`

**Interfaces:**
- Produces: `DatabaseCategoryBank(sessions)` satisfying `services.ports.CategoryBank`.
- Consumes: `Transaction` (plan 2's `TransactionContext`, which carries `.session`).

- [ ] **Step 1: Write the failing tests**

```python
async def test_it_draws_only_active_categories(...)
async def test_it_draws_only_ordinary_categories_not_secrets(...)
    """§8: a secret is «та же сущность с пометкой `is_secret`», and §3.4
    puts each player's secret on their own cell — drawn from the roster,
    not from the ordinary pool.

    Kills on: dropping the `is_secret` filter, which would deal somebody
    else's secret as an ordinary cell and reveal it (§3.4 sets
    `revealed=True` on every ordinary cell)."""
async def test_it_never_draws_the_same_category_twice(...)
async def test_it_honours_the_exclusion_set(...)
    """The exclusion is this match's secrets. Kills on: ignoring it, which
    puts a player's own secret on the board twice — once hidden as theirs,
    once revealed as an ordinary cell."""
async def test_it_raises_content_exhausted_when_the_library_is_too_small(...)
    """§8. Kills on: returning a short tuple, which `_deal` would then zip
    against the cells and produce a board with fewer categories than cells
    — an invariant §2.8 does not survive."""
async def test_it_takes_for_share_on_every_category_it_draws(...)
    """§5.3, asserted against PostgreSQL itself: a second transaction that
    tries `SELECT … FOR UPDATE` on a drawn row blocks while the drawing
    transaction is open.

    Kills on: dropping `.with_for_share()`, which lets a concurrent edit
    bump a category's version — and change its images — between selection
    and the commit that writes the deal."""
async def test_a_short_pack_is_drawn_whole_rather_than_refused(...)
    """Ruling 1: a category with 40 images is playable. Kills on: raising
    when fewer than `count` exist, which would make every category below 60
    images unusable and quietly shrink the library to nothing."""
async def test_a_category_with_no_active_images_is_content_exhausted(...)
async def test_the_pack_order_is_not_the_position_order(...)
    """Ruling 2. Kills on: `ORDER BY position`, which makes a category's
    second showing in one evening open with the picture the room already
    saw."""
async def test_the_pack_contains_no_inactive_image(...)
```

- [ ] **Step 2: Write `library/bank.py`**

`draw_categories` selects `id` from `categories` where `is_active`, not
`is_secret`, `id` not in `exclude`, `ORDER BY random()`, `LIMIT count`,
`FOR SHARE`; raises `ContentExhausted` when it gets fewer than `count`.
`draw_images` selects active image ids for the category, `ORDER BY
random()`, `LIMIT count`, and raises `ContentExhausted` only on zero.

Both run on `tx.session` — the caller's transaction — because that is what
makes the `FOR SHARE` locks live until the commit (§5.3, §6.3).

- [ ] **Step 3:** Run the tests, `mypy`, `ruff check`.

- [ ] **Step 4: Commit** `"Draw a board's categories under the lock §5.3 asks for"`.

---

### Task 4: `ContentDirectory`, and the answer that only ever goes one way

**Files:**
- Create: `backend/src/budge/library/directory.py`
- Test: `backend/tests/library/test_directory.py`

**Interfaces:**
- Produces: `DatabaseContentDirectory(sessions)` satisfying `services.ports.ContentDirectory`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_it_names_the_categories_it_was_asked_about(...)
async def test_it_answers_the_images_it_was_asked_about(...)
async def test_it_says_nothing_about_what_it_was_not_asked(...)
    """The property plan 4's ruling 1 rests on: the stage projection is
    leak-proof because it never *asks*, which is only true if a directory
    answers exactly the question it was given.

    Kills on: returning every row in the table — the stage projection would
    then have every answer in scope, and its leak-proofness would go back
    to depending on the frame layer alone."""
async def test_an_unknown_id_is_absent_rather_than_null(...)
    """The contract plan 4 fixed: a missing key and a `None` value are the
    same fact, and carrying both would give every reader two branches."""
async def test_it_names_an_inactive_category(...)
    """§5.3: «поздняя правка библиотеки не может задним числом изменить уже
    сыгранную дуэль». A category deactivated mid-match is still the
    category on the board, and a frame that stopped naming it would blank
    a group the room is looking at.

    Kills on: filtering on `is_active` here — selection filters, display
    must not."""
async def test_it_answers_for_an_inactive_image(...)
    """Same reason: the pack was drawn into `AttackDeclared` and is
    immutable. Kills on: filtering, which would leave the operator with a
    blank answer mid-duel for a picture the room can see."""
async def test_an_empty_request_touches_the_database_not_at_all(...)
    """A frame with no revealed groups is the common case during setup, and
    it must not cost two queries.

    Kills on: issuing `SELECT … WHERE id IN ()` twice per frame."""
```

- [ ] **Step 2: Write `library/directory.py`**

One session, two `SELECT … WHERE id IN (…)`, skipped entirely when the
corresponding set is empty. No `is_active` filter, for the reason the two
tests above give.

- [ ] **Step 3:** Run, `mypy`, `ruff check`.

- [ ] **Step 4: Commit** `"Name a category and answer an image, and nothing beyond the question"`.

---

### Task 5: The admin surface

**Files:**
- Create: `backend/src/budge/api/schemas/library.py`
- Create: `backend/src/budge/api/routes/library.py`
- Modify: `backend/src/budge/api/app.py` (mount the router)
- Modify: `backend/src/budge/contracts/schema.py` (new roots)
- Modify: `backend/tests/api/test_commands.py` (`INBOUND_MODULES` gains the module)
- Test: `backend/tests/api/test_library_routes.py`
- Regenerate: `frontend/src/shared/api/contracts.ts`

- [ ] **Step 1: The bodies**

`CreateCategoryBody(title, is_secret)`, `EditCategoryBody(title, is_secret)`,
`SetActiveBody(is_active)`, `AddImageBody(media_sha256, answer_text)`,
`EditImageBody(media_sha256, answer_text)`, `ReorderImagesBody(image_ids)`,
and the responses `CategorySummaryBody(id, title, is_secret, is_active,
version, active_image_count)`, `ImageBody(id, media_sha256, answer_text,
position, is_active)`, `CategoryDetailBody(category, images)`,
`ReadinessBody(cells, ordinary_available, secrets_available, thin, ready)`.

`media_sha256` is `Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]` —
ruling 8's one property, mirrored from the check constraint so the API
refuses it before the database has to.

- [ ] **Step 2: The routes**

All under `/api/library`, all behind `Depends(require_host)` on the router
exactly as `matches.py` does it. Every write calls `LibraryCatalogue`;
none touches a model directly, which Task 2's import-graph test enforces.

`GET /api/library/readiness?cells=N` answers ruling 7's question: how many
ordinary and secret categories are active, which of them have fewer than
`thin_image_threshold` active images, and whether a board of `cells` cells
can be filled at all. It refuses nothing.

- [ ] **Step 3: `ApiSettings` gains `thin_image_threshold: int = 40`**

§8 calls the threshold «настраиваемый». 40 rather than 60: `IMAGE_PACK_SIZE`
is what a duel *may* consume, and ruling 1 makes a shorter pack legal — so
the warning fires where an operator would want to top a category up, not
wherever the pack size happens to sit.

- [ ] **Step 4: Tests**

```python
async def test_every_library_route_refuses_an_unauthenticated_caller(...)
    """Parametrized over all of them, for the reason plan 4's equivalent
    gives: forgetting the dependency on one route is the failure a
    per-route test set is least likely to catch."""
async def test_a_category_can_be_created_listed_and_edited(...)
async def test_editing_a_category_bumps_the_version_over_the_wire(...)
async def test_deactivating_hides_a_category_from_selection_but_not_from_the_list(...)
async def test_an_image_digest_that_is_not_a_sha256_is_refused_by_the_api(...)
    """Ruling 8. Kills on: dropping the pattern, which pushes the refusal
    down to the check constraint and turns a 422 into a 500."""
async def test_the_listing_carries_the_active_image_count(...)
async def test_readiness_names_the_thin_categories(...)
    """§8's «мягкое предупреждение». Kills on: making it a hard refusal —
    §8 says the operator is warned, not blocked."""
async def test_readiness_is_not_ready_when_the_library_cannot_fill_the_board(...)
async def test_no_library_route_deletes_anything(...)
    """§5.3's «мягко», at the routing layer: no route is registered with
    the DELETE method against a category or an image."""
```

- [ ] **Step 5:** Add the new roots to `contracts/schema.py`, run
  `budge export-types`, commit the regenerated file. Plan 5's
  `test_every_schema_model_is_reachable_from_a_root` fails first if a body
  is forgotten — which is the point of it.

- [ ] **Step 6:** `pytest -q`, `mypy`, `ruff check`, `budge export-types --check`.

- [ ] **Step 7: Commit** `"Give the operator a library to keep"`.

---

### Task 6: Wire it in, and watch a board actually deal

The payoff. Plan 4 wired `UnavailableCategories` and `UnavailableContent`
and asserted that a deal refuses cleanly; this replaces both and asserts
that it now succeeds.

**Files:**
- Modify: `backend/src/budge/api/app.py`
- Modify: `backend/tests/api/test_wiring.py`
- Test: `backend/tests/api/test_a_whole_match.py`

- [ ] **Step 1: Replace the two unavailable leaves in `build_app`**

`DatabaseCategoryBank(sessions)` for the materialiser's bank, and
`CachingContentDirectory(DatabaseContentDirectory(sessions))` for the
directory — the cache stays, and ruling 10 of plan 4 (never cache a miss)
is what makes it safe against a library the operator is still filling in.

- [ ] **Step 2: Update `test_dealing_with_no_library_is_a_rejection_not_a_quarantine`**

It is now a test about an *empty* library rather than an absent one: with
`DatabaseCategoryBank` wired and no categories created, the deal must still
refuse with `content_unavailable` and must still not quarantine. Keep the
name; the property is unchanged and it is §8's, not plan 4's.

- [ ] **Step 3: Write `tests/api/test_a_whole_match.py`**

One test, over the real HTTP surface, that does what a show does: log in,
create a library with enough categories and images, create a match, add two
players, assign each a secret, deal, start, declare an attack, start the
duel, judge, and watch the stage socket's frames along the way.

```python
async def test_a_match_can_be_played_from_an_empty_database(...) -> None:
    """Everything five plans built, exercised as one sequence.

    Each plan's suite proves its own layer; this proves they compose — a
    deal that draws from the library, a pack drawn at declaration, a frame
    on the stage socket naming the revealed category and carrying no
    answer, and the same frame on the host socket carrying one.

    Kills on: any seam that was only ever tested against a fake — a bank
    whose `FOR SHARE` deadlocks against the appending transaction, a
    directory whose ids do not match what selection drew, a projection
    asking for a category the deal never created."""
```

This is the first test in the repository that touches every layer at once,
and it is worth its runtime for exactly that reason.

- [ ] **Step 4:** `pytest -q`, `mypy`, `ruff check`, `budge export-types --check`.

- [ ] **Step 5: Commit** `"Deal a real board from a real library"`.

- [ ] **Step 6:** Use superpowers:finishing-a-development-branch.
