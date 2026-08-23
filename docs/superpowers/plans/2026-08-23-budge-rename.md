# Renaming podvinsya to budge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project from `podvinsya` to `budge` everywhere — the Python package, the CLI, the environment prefix, the test and deployment identifiers, the product-facing strings, and the documentation — leaving no occurrence behind and no test silently disabled.

**Architecture:** Not a single `sed`. The occurrences fall into seven coupled sets, preceded by one fix the rename would otherwise entrench, and each set has to move whole or something breaks — sometimes loudly, sometimes not (R1). One task per set, the suite green at every commit, so a mistake is bisectable to one coupling rather than to a 1500-line diff.

**Tech Stack:** No new dependencies. `git mv`, `sed`, `pip install -e .`, and the existing test suite.

**One thing here is not a rename.** Task 1 fixes how `cli.py` locates `alembic.ini`. It is in this plan rather than in the infrastructure one because the rename moves that file anyway, and fixing the lookup first means the ini travels with the package instead of being renamed in place and left fragile.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md`. Note the spec's *body* never uses the word — only its filename does (Task 7).

**Predecessor:** `docs/superpowers/plans/2026-08-23-budge-infrastructure.md` on `feature/infra`. **This plan must run after that one is complete**, because infra creates `compose.yaml`, `.env.example` and `backend/scripts/backup-loop.sh`, all of which carry `PODVINSYA_*` variables and `podvinsya` CLI invocations that Tasks 3 and 4 have to rename. Starting before infra lands means renaming a moving target.

## Global Constraints

- **Branch `feature/rename`, created off `feature/infra`.** Never commit to `main`.
- **The suite must be green at every commit**: `cd backend && pytest -q`, `mypy --strict src tests`, `ruff check .`. A commit that leaves it red has no value as a bisect point, which is the entire reason this is eight tasks instead of one.
- **After the package directory moves, the editable install is stale** and every test fails on import (R8). `pip install -e .` before concluding anything is broken.
- **Never hand-edit `frontend/src/shared/api/contracts.ts`.** It is generated; regenerate it (R4).
- **Do not touch `docs/superpowers/specs/`'s body.** It contains no occurrences. Its filename changes in Task 7.
- The frontend needs almost nothing: three occurrences, all in comments and a generated header.

---

## The inventory this plan is written against

1585 occurrences across 152 files (`podvinsya` 1528, `PODVINSYA` 51, `Podvinsya` 6), plus 9 paths whose *name* carries the word. Excluding documentation, the code-and-config surface is 793 occurrences across 142 files.

| Set | Where | Moves in |
| --- | --- | --- |
| Package directory, 703 import lines, packaging metadata | `backend/src/podvinsya/`, `pyproject.toml`, `alembic.ini` | Task 7 |
| CLI name and every invoker, the generated header | `pyproject.toml`, `cli.py`, `ci.yml`, `Dockerfile`, READMEs, `typescript.py` | Task 7 |
| `PODVINSYA_` env prefix, and the two test-only `PODVINSYA_TEST_*` vars | `config.py`, tests, `.env.example`, `compose.yaml`, `backup-loop.sh` | Task 7 |
| Postgres and MinIO identifiers — coupled across three files | `compose.test.yaml`, `ci.yml`, `tests/support/db.py` | Task 7 |
| Product-facing: FastAPI title, session cookie, S3 bucket default | `app.py`, `principal.py`, `api/settings.py` | Task 7 |
| Documentation filenames and bodies | `docs/superpowers/**` | Task 7 |
| — | — | Task 8 reviews |

---

## Rulings

**R1 — Ordered by coupling, not by file type.** A repo-wide `sed -i 's/podvinsya/budge/g'` would rename the package, the test-only override variables, the Postgres role and the session cookie in one commit. If anything then failed, there would be no green checkpoint between "before" and "1500 lines later". Each task below moves one coupled set completely and leaves the suite green.

**R2 — Three tests can pass *vacuously* after a partial rename, and they are guarded before anything moves.** This is the most important ruling here, because each of these tests exists to enforce a ruling from an earlier plan, and a rename that silently disables one removes the enforcement while leaving a green tick:

- `backend/tests/api/test_stage_ws.py:36` — `FORBIDDEN_MODULES = ("podvinsya.domain.actions", "podvinsya.runtime")`. This is the API plan's ruling 12: the stage socket must import no way to submit a command. Left un-renamed, the guard matches nothing and passes.
- `backend/tests/domain/test_purity.py:6` — `pathlib.Path(...).parents[2] / "src" / "podvinsya" / "domain"`. A hardcoded path segment, not an import. Left un-renamed, it walks a directory that does not exist.
- `backend/tests/runtime/test_watchdog.py:489` and `test_match.py:585` — `caplog.at_level(..., logger="podvinsya.runtime.watchdog")`. `caplog.at_level` on a non-existent logger does not error; it captures nothing, and the assertion fails with an empty-records message that reads like a behaviour change.

Task 2 Step 1 adds an assertion to each that its target *exists* before the rename touches anything. *Cost if wrong:* the enforcement those tests provide disappears silently, and nothing in the suite would ever say so.

**R3 — `argparse`'s `prog="podvinsya"` gets a test before it is renamed.** Nothing in `backend/tests/test_cli.py` ever checks usage or help output, so this string has no coverage: a missed rename ships a CLI whose `--help` and every error message name a program that no longer exists. *Cost if wrong:* cosmetic but user-facing, and invisible to CI forever.

**R4 — The generated contract is regenerated, never edited.** `backend/src/podvinsya/contracts/typescript.py:18-19` emits a header that must byte-match `frontend/src/shared/api/contracts.ts:1-2`, and CI enforces it with `export-types --check`. Rename the generator, then run the generator. *Cost if wrong:* CI red, loudly — this one cannot fail silently, which is why it is a ruling about method rather than about risk.

**R5 — The S3 bucket default becomes `budge-media`.** `backend/src/podvinsya/api/settings.py:32` is the only value in the tree that a real deployment would carry by default, and renaming it would silently 404 every stored image for a deployment that already held objects under `podvinsya-media`. There is no such deployment: nothing has been deployed, and the infra plan already sets `PODVINSYA_S3_BUCKET: budge-media` explicitly, so the default is currently overridden anyway. *Cost if wrong:* if a live store ever did exist, its objects would have to be copied to the new bucket or the env override kept. `docs/operations.md` gains a line saying so.

**R6 — The session cookie becomes `budge_session`.** Every live operator session is silently invalidated — the browser keeps sending a cookie the server no longer looks for, and the operator lands on the login screen. There are no live sessions. *Cost if wrong:* one unexpected logout, recoverable by logging in.

**R7 — Documentation moves last, mechanically, in its own commit.** 792 of the 1585 occurrences are in `docs/`, and none of them can break anything. Mixing them into the code commits would bury a 793-occurrence code change inside a 1585-occurrence diff. The bodies of completed plan documents *are* rewritten rather than left as historical record: a plan that says `src/podvinsya/api/` is actively misleading to anyone who follows it after this. Git history keeps the originals. *Cost if wrong:* the plan documents no longer match the commit messages of the work they describe.

**R8 — The editable install must be recreated the moment the package directory moves.** `backend/.venv/` holds `bin/podvinsya`, `_editable_impl_podvinsya.pth` and a `podvinsya-0.1.0.dist-info/`. Until `pip install -e .` re-runs, every single test fails at import — which looks exactly like the rename having gone wrong. *Cost if wrong:* an hour spent debugging a working rename.

---

## Task 1: Making `alembic.ini` findable

A bug the rename would otherwise entrench, fixed first so the file travels with the package when Task 2 moves it.

`cli.py` locates two things. The migrations directory it finds through the package — `Path(podvinsya.db.__file__).parent / "migrations"` — which is correct in every install layout. `alembic.ini` it finds by walking three parents up from `__file__`, which is correct only when the package sits in a source tree. Installed flat into `site-packages`, the same arithmetic yields `/usr/local/lib/python3.12/alembic.ini`, and `migrate` dies with `FileNotFoundError` inside `env.py`'s `fileConfig` before it opens a connection.

That is not hypothetical: the infrastructure plan's API image hit it, and the workaround was to make the production image an *editable* install so the source layout survives into the container. Nothing tests that, so a later tidy-up of the Dockerfile reintroduces it silently.

**Files:**
- Move: `backend/alembic.ini` → `backend/src/podvinsya/alembic.ini`
- Modify: `backend/src/podvinsya/cli.py`, `backend/tests/support/db.py`, `backend/pyproject.toml`
- Create: a test in `backend/tests/test_cli.py`

**Interfaces:**
- Produces: `ALEMBIC_INI` resolving inside the package in every layout. No signature changes.

- [x] **Step 1: Write the failing test — append to `backend/tests/test_cli.py`**

```python
def test_the_alembic_ini_travels_with_the_package() -> None:
    """`migrate` reads an ini file, and where it looks for it has to be
    correct in an installed layout as well as in a source tree.

    Locating it by walking parents up from `cli.py` is only right when the
    package sits under `backend/src/`; installed flat into site-packages
    the same arithmetic points at the interpreter's lib directory, and
    `migrate` dies in `env.py`'s `fileConfig` before it opens a connection.
    Living inside the package makes it findable the same way the
    migrations directory already is.

    Kills on: the parent-walk — `backend/alembic.ini`'s parent is
    `backend/`, not the package directory.
    """
    import podvinsya

    from podvinsya.cli import ALEMBIC_INI

    assert ALEMBIC_INI.is_file()
    assert ALEMBIC_INI.parent == Path(podvinsya.__file__).parent
```

with `from pathlib import Path` at the top if it is not already imported.

- [x] **Step 2: Run it and watch it fail**

```bash
cd backend && pytest tests/test_cli.py -q -k alembic_ini; echo "exit=$?"
```

Expected: FAIL on the second assertion — the file is currently at `backend/alembic.ini`, whose parent is `backend`.

- [x] **Step 3: Move the ini into the package**

```bash
cd backend && git mv alembic.ini src/podvinsya/alembic.ini; echo "exit=$?"
```

`script_location` inside it stays `src/podvinsya/db/migrations`. It is relative and Alembic resolves it against the *invocation* directory, which is why `cli.py` already overrides it — the value in the file matters only to a bare `alembic` invocation from `backend/`, which now needs `-c src/podvinsya/alembic.ini`.

- [x] **Step 4: Locate it through the package**

In `backend/src/podvinsya/cli.py`, replace the `ALEMBIC_INI` assignment:

```python
# Located through the package, exactly as `_config` already locates the
# migrations directory below. The previous form walked three parents up
# from this file, which is `backend/` in a source tree and the
# interpreter's lib directory in a flat install — so `migrate` worked from
# a checkout and died in an installed container.
ALEMBIC_INI = Path(podvinsya.__file__).resolve().parent / "alembic.ini"
```

`import podvinsya` is needed alongside the existing `import podvinsya.db`; importing the subpackage already binds the parent name, so confirm whether a second import line is required or whether `ruff` flags it as redundant, and do whichever keeps both `ruff` and `mypy --strict` clean.

- [x] **Step 5: Keep it in the wheel**

`backend/pyproject.toml` line 35 is `packages = ["src/podvinsya"]`. Hatchling includes non-Python files under a declared package directory, so the ini should ship — but *should* is not evidence, and this is the whole point of the task. Prove it with a real non-editable install:

```bash
cd backend
python -m venv /tmp/budge-wheel-probe
/tmp/budge-wheel-probe/bin/pip install --quiet . ; echo "install=$?"
/tmp/budge-wheel-probe/bin/python -c "
from podvinsya.cli import ALEMBIC_INI
print(ALEMBIC_INI, ALEMBIC_INI.is_file())
"; echo "exit=$?"
rm -rf /tmp/budge-wheel-probe
```

Expected: the path prints under `site-packages/podvinsya/alembic.ini` and `True`. If it prints `False`, the ini is not being packaged — add it explicitly:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/podvinsya/alembic.ini" = "podvinsya/alembic.ini"
```

and re-run the probe. Report which was needed.

- [x] **Step 6: Point the test suite's own helper at the new location**

`backend/tests/support/db.py` has `ALEMBIC_INI = BACKEND_DIR / "alembic.ini"`. Change it to locate the file the same way, so the suite and the CLI cannot disagree:

```python
ALEMBIC_INI = Path(podvinsya.__file__).resolve().parent / "alembic.ini"
```

`BACKEND_DIR` is still used for nothing else in that module — if it becomes unused, remove it rather than leaving a name `ruff` will flag.

- [x] **Step 7: Green everything**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
```

Expected: all 0, one test more than before.

- [x] **Step 8: Drop the editable-install workaround from the image**

`backend/Dockerfile`'s second install is `pip install --no-deps -e .`, made editable in the infrastructure plan solely to keep the source layout alive so this parent-walk resolved. With the ini inside the package that is no longer needed:

```dockerfile
RUN pip install --no-cache-dir --no-deps .
```

and the `COPY alembic.ini ./` line, if present, can go — the file is now inside `src/`. Prove the image still migrates:

```bash
cd /home/alexey/projects/sandbox/budge-game
docker compose build api; echo "build=$?"
docker compose up -d postgres; echo "up=$?"
sleep 8
docker compose run --rm migrate; echo "migrate=$?"
docker compose down -v
```

Expected: `migrate=0`, with Alembic's `Running upgrade` lines in the output. **If this fails, revert the Dockerfile to `-e` and report it** — the workaround is not wrong, and shipping a broken image to prove a point is.

- [x] **Step 9: Commit**

```bash
git add backend && git commit -m "fix: let the CLI find its alembic.ini in an installed layout

Located by walking parents up from cli.py, which is backend/ in a source
tree and the interpreter's lib directory once installed — so migrate
worked from a checkout and died in a container. It now travels inside the
package and is found the same way the migrations directory already was,
which is what let the image drop its editable-install workaround."
```

---

## Task 2: The package

The directory, 703 import lines, the packaging metadata, and the four tests that reference the name as a string rather than an import. The CLI keeps its old *name* here — only its target module moves.

**Files:**
- Move: `backend/src/podvinsya/` → `backend/src/budge/` (81 files across 14 directories)
- Modify: `backend/pyproject.toml`, `backend/src/budge/alembic.ini` (moved into the package by Task 1)
- Modify: every file with a `podvinsya` import (55 source, 77 test)
- Modify: `backend/tests/domain/test_purity.py`, `backend/tests/api/test_stage_ws.py`, `backend/tests/runtime/test_watchdog.py`, `backend/tests/runtime/test_match.py`, `backend/tests/db/test_migrations.py`

- [x] **Step 1: Guard the three tests that could pass vacuously — BEFORE anything moves**

R2. Each of these currently enforces a ruling from an earlier plan by naming a module or path as a string. Add the assertion that the target exists, run the suite, and confirm it is still green *now* — that is what proves the guard is correct before the rename can invalidate it.

In `backend/tests/domain/test_purity.py`, immediately after the `DOMAIN` assignment:

```python
# The path is spelled out rather than derived from an import, so a rename
# of the package would leave this pointing at a directory that no longer
# exists — and `iterdir()` on nothing walks nothing and passes. Fail here
# instead, loudly, before the walk that is supposed to be the test.
assert DOMAIN.is_dir(), f"the domain package is not at {DOMAIN}"
```

In `backend/tests/api/test_stage_ws.py`, after `FORBIDDEN_MODULES`:

```python
# These are strings, and the guard they feed matches nothing if they name
# modules that do not exist — so it would pass vacuously through exactly
# the rename most likely to invalidate it. Ruling 12 is what this test
# holds; import the modules to prove they are still there to be forbidden.
for _forbidden in FORBIDDEN_MODULES:
    importlib.import_module(_forbidden)
```

with `import importlib` at the top.

In `backend/tests/runtime/test_watchdog.py` and `backend/tests/runtime/test_match.py`, each `caplog.at_level(..., logger="podvinsya.runtime.X")` names a logger as a string. `caplog.at_level` accepts any name — a logger nobody has created simply captures nothing — so the check is that the *module* whose `__name__` becomes that logger name is importable. Add once at module scope in each file, with `import importlib` at the top:

```python
# The logger name below is a string, and `caplog.at_level` accepts any
# string: a name that no module produces captures nothing and the
# assertion then fails with an empty-records message that reads like a
# behaviour change. Import the module that owns the logger instead, so a
# rename that orphans the name fails here and says why.
importlib.import_module("podvinsya.runtime.watchdog")
```

In `test_match.py` the module is `podvinsya.runtime.match`.

- [x] **Step 2: Run the suite and confirm the guards pass before the rename**

```bash
cd backend && pytest -q; echo "pytest=$?"
```

Expected: green, same test count as before. If a guard fails now, it has found a pre-existing bug — stop and report it rather than renaming around it.

- [x] **Step 3: Commit the guards on their own**

They are the safety net for everything after, and they must be provably green *before* the move.

```bash
cd .. && git add backend/tests && git commit -m "test: fail loudly if a name-shaped guard stops naming anything"
```

- [ ] **Step 4: Move the package**

```bash
cd backend && git mv src/podvinsya src/budge; echo "exit=$?"
```

- [ ] **Step 5: Rewrite every import**

```bash
cd backend
grep -rl 'podvinsya' src tests --include='*.py' --include='*.mako' \
  | xargs sed -i 's/\bpodvinsya\b/budge/g'
echo "exit=$?"
```

`\b` word boundaries matter: without them this would also rewrite `podvinsya_test`, `podvinsya-media` and `podvinsya_session`, which belong to Tasks 4 and 5 and must not move yet (R1). Verify nothing outside the intended set changed:

```bash
cd backend && git diff --stat | tail -3
grep -rn 'podvinsya' src tests --include='*.py' | grep -v 'podvinsya_test\|podvinsya-media\|podvinsya_session\|PODVINSYA_' || echo "ok: only the deferred sets remain"
```

That grep is restricted to `*.py`, so it will not show `src/budge/alembic.ini` — whose `script_location` still names the old path. Step 6 handles it; check it explicitly rather than trusting the line above:

```bash
cd backend && grep -n 'podvinsya' src/budge/alembic.ini || echo "ok: the ini is clean"
```

- [ ] **Step 6: Update the packaging metadata**

`backend/pyproject.toml`:
- line 2: `name = "podvinsya"` → `name = "budge"`
- line 28: `podvinsya = "podvinsya.cli:main"` → `podvinsya = "budge.cli:main"` — **the script name stays `podvinsya` for now**; Task 3 renames it. Only its target moves here.
- line 35: `packages = ["src/podvinsya"]` → `["src/budge"]`
- line 50: `files = ["src/podvinsya", "tests"]` → `["src/budge", "tests"]`
- line 51: `exclude = ["src/podvinsya/db/migrations/versions/"]` → `["src/budge/db/migrations/versions/"]`

`backend/src/budge/alembic.ini` line 2 — Task 1 moved this file inside the package, so `git mv` in Step 4 has already carried it across; only its *contents* still name the old path. `script_location = src/podvinsya/db/migrations` → `src/budge/db/migrations`.

Note `script_location` is *overridden at runtime* by `cli.py`, so a mistake here is invisible to `podvinsya migrate` and shows up only under a bare `alembic -c src/budge/alembic.ini` invocation. Change it anyway: the test suite exercises the code path, not the ini, so nothing else will catch it.

- [ ] **Step 7: Recreate the editable install**

R8. Until this runs, every test fails at import and it looks like the rename broke everything.

```bash
cd backend && pip install -e . ; echo "exit=$?"
python -c 'import budge, budge.cli; print(budge.__file__)'; echo "exit=$?"
```

Expected: the path prints under `src/budge/`. If a stale `_editable_impl_podvinsya.pth` shadows it, remove it from `site-packages` and reinstall — report if you had to.

- [ ] **Step 8: Run everything**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
```

Expected: all 0, with the same test count as Step 2. A *lower* count means a test file stopped being collected — find it before continuing.

- [ ] **Step 9: Confirm the guards still guard**

The point of R2 is that these fail if their target vanished. Prove they would:

```bash
cd backend
sed -i 's/"budge.domain.actions"/"podvinsya.domain.actions"/' tests/api/test_stage_ws.py
pytest tests/api/test_stage_ws.py -q; echo "exit=$?"
git checkout -- tests/api/test_stage_ws.py
```

Expected: exit **1** with a `ModuleNotFoundError` from the guard — not a pass. If it passes, the guard is not doing its job and Task 8's review will find nothing; fix it now.

- [ ] **Step 10: Commit**

```bash
cd .. && git add backend && git commit -m "refactor: move the package from podvinsya to budge"
```

---

## Task 3: The CLI and the generated header

The console-script name, `prog=`, and every invoker — including the generator whose output CI diffs.

**Files:**
- Modify: `backend/pyproject.toml`, `backend/src/budge/cli.py`, `backend/src/budge/contracts/typescript.py`
- Modify: `backend/tests/test_cli.py`, `backend/tests/contracts/test_typescript.py`
- Modify: `.github/workflows/ci.yml`, `backend/Dockerfile`, `frontend/README.md`, `frontend/src/shared/api/index.ts`
- Modify (generated): `frontend/src/shared/api/contracts.ts`
- Modify: `backend/src/budge/api/app.py`, `api/settings.py`, `api/routes/session.py` docstrings; `backend/scripts/backup-loop.sh`, `compose.yaml`, `docs/operations.md` (all from the infra plan)

- [ ] **Step 1: Write the missing test for `prog` — R3**

Nothing covers it. Add to `backend/tests/test_cli.py`:

```python
def test_the_help_names_the_program_the_user_typed(capsys: pytest.CaptureFixture[str]) -> None:
    """`prog=` has no other coverage, so a missed rename here would ship a
    CLI whose --help and every error message name a program that does not
    exist — and nothing would ever say so.

    Kills on: leaving `prog="podvinsya"` behind.
    """
    with pytest.raises(SystemExit):
        main(["--help"])
    assert capsys.readouterr().out.startswith("usage: budge")
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd backend && pytest tests/test_cli.py -q -k help; echo "exit=$?"
```

Expected: FAIL — `usage: podvinsya` is what it prints today.

- [ ] **Step 3: Rename the script and `prog`**

`backend/pyproject.toml` line 28: `podvinsya = "budge.cli:main"` → `budge = "budge.cli:main"`.
`backend/src/budge/cli.py` line 39: `prog="podvinsya"` → `prog="budge"`.

Then reinstall so the new console script exists:

```bash
cd backend && pip install -e . ; echo "exit=$?"
which budge; budge --help | head -3; echo "exit=$?"
```

The old `podvinsya` shim may linger in `.venv/bin`; remove it so a stale invoker fails loudly rather than working by accident.

- [ ] **Step 4: Rename the generator's header, then regenerate**

R4. `backend/src/budge/contracts/typescript.py` lines 18–19:

```python
HEADER = """// Generated from the Pydantic models by `budge export-types`.
// Do not edit: run `budge export-types` and commit the result.
```

Update `backend/tests/contracts/test_typescript.py:178` to assert `"budge export-types"`. Then regenerate rather than editing the artifact:

```bash
cd backend && budge export-types; echo "exit=$?"
budge export-types --check; echo "check=$?"
```

Expected: both 0, and `git diff frontend/src/shared/api/contracts.ts` shows only the two header lines changed.

- [ ] **Step 5: Rename every invoker**

| File | Change |
| --- | --- |
| `.github/workflows/ci.yml:79` | `run: podvinsya export-types --check` → `budge` |
| `backend/Dockerfile` | `CMD ["podvinsya", "serve", ...]` → `["budge", ...]`; the comment on line 5 |
| `frontend/README.md:40,43` | both `podvinsya export-types` mentions |
| `frontend/src/shared/api/index.ts:3` | the comment |
| `backend/src/budge/cli.py` | comments at 22, 31, and the "Run `podvinsya export-types`" message at 102 |
| `backend/src/budge/api/app.py:5`, `api/settings.py:4` | docstrings naming `podvinsya migrate` |
| `backend/src/budge/api/routes/session.py:5` | docstring naming `podvinsya hash-password` |
| `backend/tests/db/test_migrations.py:120`, `tests/support/db.py:44` | docstrings |
| `compose.yaml` | `command: ["podvinsya", "migrate"]` |
| `backend/scripts/backup-loop.sh` | both `podvinsya backup` and `podvinsya restore-drill` |
| `docs/operations.md` | every `podvinsya` invocation |

The last three exist only if the infra plan has landed. If any is absent, say so rather than creating it.

- [ ] **Step 6: Green everything and commit**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd .. && git add -A && git commit -m "refactor: rename the CLI to budge, and regenerate the contract header"
```

- [ ] **Step 7: Prove no invoker was missed**

```bash
grep -rn 'podvinsya \(serve\|migrate\|backup\|export-types\|hash-password\|restore-drill\)' \
  --include='*' . 2>/dev/null | grep -v node_modules | grep -v '^./docs/superpowers/plans' \
  || echo "ok: every invoker renamed"
```

Expected: `ok: every invoker renamed`. Plan documents are excluded because Task 7 handles them.

---

## Task 4: The environment prefix

`PODVINSYA_` → `BUDGE_`, in one commit, because pydantic-settings ignores unknown variables: a half-renamed prefix does not say "you renamed half the prefix", it says `field required`.

**Files:**
- Modify: `backend/src/budge/config.py`, `backend/src/budge/cli.py`, `backend/src/budge/api/routes/session.py`
- Modify: `backend/tests/test_cli.py`, `tests/db/test_migrations.py`, `tests/api/conftest.py`, `tests/api/test_security.py`, `tests/support/db.py`
- Modify: `.env.example`, `compose.yaml` (both from the infra plan)

- [ ] **Step 1: Rename the prefix and every setter together**

The single definition is `backend/src/budge/config.py:12` — `env_prefix="PODVINSYA_"` → `"BUDGE_"`.

Then every place a prefixed variable is set or read:

| File | Lines | What |
| --- | --- | --- |
| `backend/src/budge/config.py` | 8 | docstring naming `PODVINSYA_DATABASE_URL` |
| `backend/src/budge/cli.py` | 45 | help text naming `PODVINSYA_HOST_PASSWORD` |
| `backend/src/budge/api/routes/session.py` | 5 | docstring |
| `backend/tests/test_cli.py` | 58–63, 79 | six `setenv` calls and one `delenv` |
| `backend/tests/db/test_migrations.py` | 104, 125 | two `setenv` calls |
| `backend/tests/api/conftest.py` | 48 | docstring `PODVINSYA_*` |
| `backend/tests/api/test_security.py` | 55 | docstring |
| `.env.example`, `compose.yaml` | — | `PODVINSYA_SECRET_KEY`, `_HOST_PASSWORD`, `_DATABASE_URL`, `_S3_*` |

A `sed` over the uppercase form is safe here — it is a distinct token from everything Tasks 5 and 6 own:

```bash
cd backend && grep -rl 'PODVINSYA_' src tests | xargs sed -i 's/PODVINSYA_/BUDGE_/g'; echo "exit=$?"
cd .. && grep -rl 'PODVINSYA_' .env.example compose.yaml 2>/dev/null | xargs -r sed -i 's/PODVINSYA_/BUDGE_/g'; echo "exit=$?"
```

- [ ] **Step 2: Catch the two variables the prefix rename does not reach**

`backend/tests/support/db.py:22,29` read `PODVINSYA_TEST_DATABASE_URL` and `PODVINSYA_TEST_S3_ENDPOINT`. These are **not** derived from `env_prefix` — pydantic-settings never sees them; the test suite reads them directly with `os.environ.get`. Anyone searching from `config.py` would miss them entirely. The `sed` above catches them because it matches the literal prefix, but confirm:

```bash
cd backend && grep -n 'BUDGE_TEST_' tests/support/db.py; echo "exit=$?"
```

Expected: both lines, now `BUDGE_TEST_DATABASE_URL` and `BUDGE_TEST_S3_ENDPOINT`.

- [ ] **Step 3: Confirm nothing anywhere still sets the old prefix**

```bash
grep -rn 'PODVINSYA_' . 2>/dev/null | grep -v node_modules | grep -v '^./docs/superpowers/plans' \
  || echo "ok: no PODVINSYA_ variable remains"
```

Expected: `ok: no PODVINSYA_ variable remains`. Plan documents are Task 7's.

- [ ] **Step 4: Green everything and commit**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd .. && git add -A && git commit -m "refactor: rename the environment prefix to BUDGE_"
```

A failure here reads `ValidationError: field required` and names the *field*, not the variable — so if it fails, look for a setter that kept the old prefix rather than for a bug in `Settings`.

---

## Task 5: The test infrastructure identifiers

Postgres role, password and database name; MinIO root credentials; the test bucket. These live in three files that must agree, and a mismatch surfaces as an authentication failure or an opaque `SignatureDoesNotMatch` rather than as a name error.

**Files:**
- Modify: `backend/compose.test.yaml`, `.github/workflows/ci.yml`, `backend/tests/support/db.py`, `backend/tests/api/test_app.py`

- [ ] **Step 1: Stop the test stack first**

The database name changes, and a running container holds the old one.

```bash
docker compose -f backend/compose.test.yaml down; echo "exit=$?"
```

- [ ] **Step 2: Rename all three files in one edit**

The coupling is the point: these six values appear in three files and nothing shares a constant between them.

| Value | `compose.test.yaml` | `ci.yml` | `tests/support/db.py` |
| --- | --- | --- | --- |
| Postgres user | 7 | 19 | inside the URL, 20 |
| Postgres password | 8 | 20 | inside the URL, 20 |
| Postgres database | 9 | 21 | inside the URL, 20 |
| Postgres healthcheck | 18 | 27 | — |
| MinIO root user | 35 | 41 | `S3_ACCESS_KEY`, 30 |
| MinIO root password | 36 | 42 | `S3_SECRET_KEY`, 31 |
| Test bucket | — | — | `S3_BUCKET`, 32 |

```bash
cd /home/alexey/projects/sandbox/budge-game
sed -i 's/podvinsya/budge/g' backend/compose.test.yaml .github/workflows/ci.yml
sed -i 's/podvinsya/budge/g' backend/tests/support/db.py backend/tests/api/test_app.py
echo "exit=$?"
```

A bare `sed` is safe in these four files: after Tasks 1–3 the only remaining occurrences in them *are* these identifiers. Verify that claim rather than trusting it:

```bash
git diff --stat backend/compose.test.yaml .github/workflows/ci.yml backend/tests/support/db.py backend/tests/api/test_app.py
git diff backend/tests/support/db.py
```

Expected: `podvinsya:podvinsya@127.0.0.1:5434/podvinsya_test` → `budge:budge@127.0.0.1:5434/budge_test`, `podvinsya-media-test` → `budge-media-test`, `podvinsya-secret` → `budge-secret`. The **port 5434 must not change** — it was chosen to stay clear of a local server and of the neighbouring triviador project.

- [ ] **Step 3: Recreate the test stack with the new identifiers**

The Postgres image only applies `POSTGRES_USER`/`POSTGRES_DB` when it initialises an empty data directory. `compose.test.yaml` keeps its data in a tmpfs, so a `down`/`up` is enough — but confirm rather than assume:

```bash
docker compose -f backend/compose.test.yaml up -d; echo "exit=$?"
sleep 5
docker compose -f backend/compose.test.yaml ps
```

Expected: both services healthy. If Postgres reports `role "budge" does not exist`, the data directory survived — `down -v` and up again.

- [ ] **Step 4: Green everything and commit**

```bash
cd backend && pytest -q; echo "pytest=$?"
cd .. && git add -A && git commit -m "refactor: rename the test database and object-store identifiers"
```

An authentication failure here means one of the three files disagrees with the other two — check the table above before looking anywhere else.

---

## Task 6: The product-facing names

Three strings a person actually sees or a deployment actually carries.

**Files:**
- Modify: `backend/src/budge/api/app.py`, `api/principal.py`, `api/settings.py`
- Modify: `backend/tests/api/test_session_routes.py`
- Modify: `docs/operations.md` (from the infra plan)

- [ ] **Step 1: The OpenAPI title**

`backend/src/budge/api/app.py:115` — `FastAPI(title="Podvinsya", ...)` → `title="budge"`. This is what a reader sees at `/docs`.

- [ ] **Step 2: The session cookie — R6**

`backend/src/budge/api/principal.py:21` — `SESSION_COOKIE = "podvinsya_session"` → `"budge_session"`.
`backend/tests/api/test_session_routes.py:69` asserts on the literal `'podvinsya_session=""'` in a `Set-Cookie` header; update it.

Note what this does: a browser holding the old cookie keeps sending it, the server no longer looks for it, and the operator silently lands on the login screen. There are no live sessions, so the cost is zero today — but say so in the commit message, because it is the kind of change that is confusing when it happens to someone.

- [ ] **Step 3: The S3 bucket default — R5**

`backend/src/budge/api/settings.py:32` — `s3_bucket: str = "podvinsya-media"` → `"budge-media"`.

This is the only value in the tree that a real deployment would carry by default. Nothing is deployed, and `compose.yaml` already sets `BUDGE_S3_BUCKET: budge-media` explicitly, so the default is currently overridden anyway. Add a line to `docs/operations.md` under a **Renaming** heading:

```markdown
## If you are restoring a deployment older than the budge rename

Objects were stored in a bucket called `podvinsya-media`, and the default
is now `budge-media`. Either set `BUDGE_S3_BUCKET=podvinsya-media` in
`.env`, or copy the objects across before starting `api`. A mismatch here
does not error — every picture simply 404s, and the game comes back
looking empty.
```

- [ ] **Step 4: Green everything and commit**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd .. && git add -A && git commit -m "refactor: rename the product-facing strings

The session cookie changes name, so any live operator session is silently
logged out — there are none today, and the browser simply stops matching a
cookie the server no longer reads."
```

---

## Task 7: The documentation

792 occurrences, eight filenames, and nothing that can break at runtime. Last, and on its own (R7).

**Files:**
- Move: 8 files under `docs/superpowers/`
- Modify: their bodies, and every cross-reference to them

- [ ] **Step 1: Rename the eight files**

```bash
cd /home/alexey/projects/sandbox/budge-game/docs/superpowers
git mv specs/2026-08-22-podvinsya-design.md specs/2026-08-22-budge-design.md
for name in 2026-08-22-podvinsya-domain-core 2026-08-22-podvinsya-persistence \
            2026-08-23-podvinsya-api 2026-08-23-podvinsya-content-library \
            2026-08-23-podvinsya-contracts 2026-08-23-podvinsya-media \
            2026-08-23-podvinsya-runtime; do
  git mv "plans/${name}.md" "plans/$(echo "$name" | sed 's/podvinsya/budge/').md"
done
echo "exit=$?"
```

- [ ] **Step 2: Rewrite the bodies**

R7: a completed plan that says `src/podvinsya/api/` is actively misleading to anyone who follows it after this rename. Git history keeps the originals.

```bash
cd /home/alexey/projects/sandbox/budge-game
grep -rl 'podvinsya\|PODVINSYA\|Podvinsya' docs/ | xargs sed -i \
  -e 's/PODVINSYA/BUDGE/g' -e 's/Podvinsya/budge/g' -e 's/podvinsya/budge/g'
echo "exit=$?"
```

Order matters: uppercase and title-case first, or the lowercase rule consumes them.

- [ ] **Step 3: Fix the cross-references to the renamed spec**

Several plans name the spec by path — `docs/superpowers/specs/2026-08-22-podvinsya-design.md` — and Step 2 has already rewritten those strings to the new name. Confirm every referenced path now resolves:

```bash
cd /home/alexey/projects/sandbox/budge-game
grep -rho 'docs/superpowers/[a-z]*/[0-9-]*[a-z-]*\.md' docs/ | sort -u | while read -r path; do
  [ -f "$path" ] || echo "DANGLING: $path"
done
echo "checked"
```

Expected: no `DANGLING:` lines. If any appears, the reference names a file that was never renamed or never existed — fix the reference, not the filename.

- [ ] **Step 4: Confirm the word is gone from the whole repository**

```bash
cd /home/alexey/projects/sandbox/budge-game
grep -rni 'podvinsya' . 2>/dev/null \
  | grep -v node_modules | grep -v '/\.git/' | grep -v '\.venv/' \
  || echo "ok: no occurrence remains anywhere"
```

Expected: `ok: no occurrence remains anywhere`. Two categories are allowed to survive and must be reported rather than silently accepted:
- anything under `backend/.venv/` — a build artefact, fixed by `pip install -e .`
- the `docs/operations.md` paragraph from Task 6 Step 3, which deliberately names the *old* bucket because that is its whole subject

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: rename podvinsya to budge throughout"
```

---

## Task 8: Review pass

The rename is mechanical, so the review is not about whether the code is right — it is about whether anything is now passing for the wrong reason.

- [ ] **Step 1: Prove the vacuous-pass guards still bite**

R2's three guards are the reason this plan is eight tasks. Confirm each fails when its target is wrong, then revert.

```bash
cd /home/alexey/projects/sandbox/budge-game/backend

sed -i 's/"budge.domain.actions"/"nonexistent.module"/' tests/api/test_stage_ws.py
pytest tests/api/test_stage_ws.py -q; echo "stage_ws=$?"
git checkout -- tests/api/test_stage_ws.py

sed -i 's|"src" / "budge" / "domain"|"src" / "nope" / "domain"|' tests/domain/test_purity.py
pytest tests/domain/test_purity.py -q; echo "purity=$?"
git checkout -- tests/domain/test_purity.py

sed -i 's/"budge.runtime.watchdog"/"nope.runtime.watchdog"/' tests/runtime/test_watchdog.py
pytest tests/runtime/test_watchdog.py -q; echo "watchdog=$?"
git checkout -- tests/runtime/test_watchdog.py
```

Expected: all three exit **1**. Any that exits 0 was passing vacuously the whole time — write the guard that catches it and report it prominently.

- [ ] **Step 2: Prove the import ban is still enforced**

The stage socket's import guard is ruling 12 from the API plan. It should fail if the ban is violated, not merely if a name is wrong:

```bash
cd backend
sed -i '/^import /a from budge.runtime import MatchManager  # deliberate violation' \
  src/budge/api/routes/stage_ws.py
pytest tests/api/test_stage_ws.py -q; echo "exit=$?"
git checkout -- src/budge/api/routes/stage_ws.py
```

Expected: exit **1**. If the import line does not typecheck, use whatever name `budge.runtime` actually exports — the point is that *some* forbidden import fails the test.

- [ ] **Step 3: Prove the CLI is really renamed**

```bash
which budge; echo "which=$?"
budge --help | head -1
command -v podvinsya && echo "STALE SHIM STILL PRESENT" || echo "ok: old command gone"
```

Expected: `budge` resolves, its usage line reads `usage: budge`, and `podvinsya` is not found. A lingering shim in `.venv/bin` is not a failure of the rename but it will let a stale invoker keep working, hiding a missed one — remove it.

- [ ] **Step 4: Prove the generated contract matches its generator**

```bash
cd backend && budge export-types --check; echo "exit=$?"
head -3 ../frontend/src/shared/api/contracts.ts
```

Expected: exit 0, and the header naming `budge export-types`.

- [ ] **Step 5: Run everything, including the front end**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd ../frontend && pnpm build; echo "build=$?"
pnpm check; echo "check=$?"
pnpm test; echo "test=$?"
```

Expected: all 0, and the backend test count identical to before Task 1. A changed count means a file stopped being collected.

- [ ] **Step 6: Prove the stack still comes up**

The rename touched `compose.yaml`, `.env.example` and the Dockerfile, and nothing in either test suite exercises those.

```bash
cd /home/alexey/projects/sandbox/budge-game
sed -i 's/PODVINSYA_/BUDGE_/g' .env 2>/dev/null || true
docker compose build; echo "build=$?"
# Task 1 dropped the image's editable-install workaround; this is the only
# place that exercises the installed layout end to end.
docker compose run --rm migrate; echo "migrate=$?"
docker compose up -d; echo "up=$?"
sleep 25
docker compose ps
curl -sS http://127.0.0.1:8080/health; echo
docker compose exec -T backup budge restore-drill --from /backups; echo "drill=$?"
docker compose down -v
```

Expected: `migrate` exited 0, `api` healthy, `/health` reporting `ok` for both checks, and the drill running under its new command name. Note the local `.env` is not committed, so its prefix has to be updated by hand — that is the one place the rename cannot reach and an operator will hit the same thing.

- [ ] **Step 7: Commit anything the review added**

```bash
git status --porcelain
git add -A && git commit -m "test: close what the rename review found"
```

If the review found nothing, say so rather than making an empty commit.

---

## Deliberately not renamed

- **Git history and commit messages.** Every commit before this branch says `podvinsya`. Rewriting them would change every SHA on four branches for no benefit; the rename is a commit, not a retcon.
- **`docs/operations.md`'s one paragraph naming `podvinsya-media`.** Its entire subject is that the old bucket exists (R5). Renaming it there would delete the warning.
- **The Alembic revision identifiers** (`0001`, `0002`). They contain no name and are referenced by `down_revision` chains and by any deployed database's `alembic_version` row.

## Done when

- `grep -rni podvinsya` over the repository, excluding `.git`, `node_modules` and `backend/.venv`, returns only the deliberate `docs/operations.md` mention.
- `cd backend && pytest -q`, `mypy --strict src tests`, `ruff check .` are green, with the same test count as before the rename.
- `cd frontend && pnpm build && pnpm check && pnpm test` are green.
- `budge --help` prints `usage: budge`; `podvinsya` is not a command.
- `budge export-types --check` is green against the committed `contracts.ts`.
- `docker compose up -d` brings the stack up and `/health` reports `ok`.
- All three vacuous-pass guards from R2 fail when their target is wrong.
- The branch `feature/rename` is left local — unpushed and unmerged.
