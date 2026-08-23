# Budge Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship §10 — the deployable stack. Images for both halves, a Docker Compose file that brings up PostgreSQL, an S3-compatible store, the API and Caddy, migrations as a separate step before the app starts, and scheduled backups **with restore drills that actually restore**.

**Architecture:** One `compose.yaml` at the repository root. The frontend is built at image-build time into static files that Caddy serves; Caddy also proxies `/api` and `/ws` to the API, so every URL the browser sees is same-origin — which is what the front end already assumes. Migrations run as a one-shot service the API waits on. Backups and drills are two new subcommands of the existing `podvinsya` CLI rather than shell scripts, because the drill has to import the domain to do its job honestly (I2).

**Tech Stack:** Docker Compose, Caddy 2, `postgres:16-alpine`, `minio/minio`, `python:3.12-slim`, `node:22-alpine` (frontend build stage only), `pg_dump`/`pg_restore` from `postgresql-client`.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md` — §10 primarily; §1.1 for the deployment profile (LAN-only, no TLS); §5.3 and §7.6 for what the media store holds; §11 for what the tests must hold.

**Predecessors:** `docs/superpowers/plans/2026-08-23-budge-stage-screen.md` and `...-budge-host-console.md`, complete on `feature/stage` and `feature/host`. This plan builds on `feature/host` and needs the frontend to exist.

## Global Constraints

- **Naming.** The product is **budge**. New files, service names, image names, volume names and documentation say "budge". The Python package, its CLI (`podvinsya`) and its environment prefix (`PODVINSYA_`) are still named `podvinsya` — renaming them is the **next** plan and is out of scope here. Where this plan must invoke the CLI or set an environment variable, it uses the existing `podvinsya` / `PODVINSYA_` names; where it names something new, it says budge.
- **§1.1 is the deployment profile: an isolated LAN, no TLS, `COOKIE_SECURE=false`.** Caddy must therefore serve plain HTTP and must **not** attempt automatic HTTPS (I7). Do not add TLS "just in case" — the session cookie is deliberately not `Secure`, and a `Secure` cookie over plain HTTP is never sent back, so the operator simply cannot log in.
- **Migrations run as a separate step before the application starts, never at import** (§10). The API service must not run migrations itself.
- **No secret has a default.** `PODVINSYA_SECRET_KEY`, `PODVINSYA_HOST_PASSWORD`, the database password and the S3 credentials all come from the environment. Compose reads them from a `.env` file that is **not** committed; `.env.example` is committed and holds no real values.
- **All work happens on branch `feature/infra`**, created off `feature/host`. Never commit to `main`.
- **`backend/compose.test.yaml` is the test stack and is not touched by this plan** beyond what Task 3 and 4 need for their own tests. The new production stack is a separate file at the repository root.
- The backend test suite must stay green: `cd backend && pytest`. It currently passes with PostgreSQL on `127.0.0.1:5434` and MinIO on `127.0.0.1:9002` from `backend/compose.test.yaml`.
- New Python code follows the existing house style: `mypy --strict` clean, `ruff` clean (line length 100), module docstrings that say *why*.

---

## What already exists

Do not rebuild these.

| Thing | Where |
| --- | --- |
| `podvinsya migrate [--revision]` | `backend/src/podvinsya/cli.py` |
| `podvinsya serve [--host] [--port]` | same; imports FastAPI lazily so `migrate` need not |
| `podvinsya hash-password` (reads stdin) | same |
| `podvinsya export-types [--check]` | same |
| `GET /health` returning `{"status", "checks": {"database", "storage"}}`, 200 or 503 | `backend/src/podvinsya/api/app.py` — §10's healthcheck, already done |
| `Settings` (`database_url`, no default) | `backend/src/podvinsya/config.py` |
| `ApiSettings` (adds `secret_key`, `host_password`, `s3_*`, …) | `backend/src/podvinsya/api/settings.py` |
| `fold(state, events)` | `backend/src/podvinsya/domain/evolve.py` |
| `MatchRepository.read_events(match_id)` | `backend/src/podvinsya/db/repository.py` |
| `S3MediaStore` | `backend/src/podvinsya/media/s3.py` |
| Test fixtures for a real database and a real bucket | `backend/tests/db/conftest.py`, `backend/tests/support/db.py` |

---

## File Structure

```
compose.yaml                          the production stack
.env.example                          every variable, no real values
Caddyfile                             plain HTTP, SPA + /api + /ws
backend/Dockerfile                    api image (also carries pg_dump — I5)
backend/.dockerignore
frontend/Dockerfile                   builds the SPA, output copied by Caddy's image
frontend/.dockerignore
docs/operations.md                    bring-up, backups, restoring for real
backend/src/podvinsya/backup/
  __init__.py
  paths.py                            where a backup lives, and how it is named
  dump.py                             take a backup: database + media
  drill.py                            restore it into a scratch database and prove it
  scratch.py                          create and drop the scratch database
backend/tests/backup/
  conftest.py
  test_paths.py
  test_dump.py
  test_drill.py
```

---

## Rulings

**I1 — A drill restores into a scratch database and never touches the live one.** The scratch database is created for the drill, named `<database>_drill_<stamp>`, and dropped when the drill ends — including when it fails. `drill.py` never opens a connection to the configured database except to read its *name*, and a test asserts the live database still holds its rows after a drill runs. *Cost if wrong:* a scheduled job that restores over production on a timer is not a backup system, it is a nightly outage. This is the one thing in this plan that must be impossible rather than merely unlikely.

**I2 — A drill proves the history folds, not that the bytes copied.** For an event-sourced system, `pg_restore` exiting 0 says the file was well-formed; it does not say the log is usable. So the drill reads every match's events out of the restored database and runs the domain's own `fold` over each one. A log that decodes but no longer evolves — a payload whose shape drifted, an event type the code has dropped — fails the drill. *Cost if wrong:* the backup is verified against the weakest possible property, and the failure surfaces on the night it matters.

**I3 — A drill cross-checks the media against the restored database.** The classic backup failure is asymmetric: rows saved and blobs missed, or the reverse. So every `images.media_sha256` in the restored database must be present in the media backup, and the drill reports any that are not. *Cost if wrong:* a restore brings the game back with every picture missing, and nothing warned anyone.

**I4 — `pg_dump --format=custom`, restored with `pg_restore`.** Not a SQL text file: the custom format is compressed, carries a table of contents, and makes `pg_restore` refuse a truncated archive rather than replaying the first half of it into a database that then looks plausible. *Cost if wrong:* a partial backup restores partially and silently.

**I5 — The API image carries `postgresql-client`, and the backup runs from that image.** This is forced by I2: the drill must import `podvinsya.domain` to fold, and it must run `pg_restore`. Two images would mean either shipping the domain into a database-tools image or shelling out from Python to a container that has the tools — both worse. *Cost if wrong:* the API image is a few megabytes larger and carries binaries the API itself never calls.

**I6 — Media is mirrored into one shared content-addressed directory, not copied per backup.** A digest names its bytes (§7.6), so a blob never changes and never needs a second copy. Each dump gets a sidecar manifest naming the digests it references, which is what makes I3's cross-check exact without duplicating gigabytes per run. Nothing ever deletes from the mirror: §5.3 deletes content only softly, and an old dump must stay restorable. *Cost if wrong:* backups grow with the whole library each night instead of with the night's additions.

**I7 — Caddy serves plain HTTP with automatic HTTPS turned off.** §1.1 puts this on an isolated network with no TLS and `COOKIE_SECURE=false`. Caddy's default is to provision certificates for any site address that looks like a domain, which on a LAN with no public DNS means it hangs retrying ACME while serving nothing. The site address is written `http://` and `auto_https off` is set explicitly. *Cost if wrong:* the stack comes up and the operator cannot reach it, with the reason buried in Caddy's logs.

**I8 — Migrations are a one-shot compose service the API waits on.** §10: «Миграции применяются отдельным шагом до старта приложения». `depends_on: { migrate: { condition: service_completed_successfully } }`. The API image's entrypoint runs `podvinsya serve` and nothing else. *Cost if wrong:* two API replicas racing the same migration, which is how an Alembic lock becomes an outage.

**I9 — The backup schedule is a loop in a container, not host cron.** The stack must be movable by copying one directory and running `docker compose up`. A crontab on the host is a second thing to install, a second thing to forget, and invisible to `docker compose ps`. The loop sleeps to the next interval and runs the two commands. *Cost if wrong:* fewer scheduling features than cron — no calendar expressions. The deployment takes one backup an hour and one drill a day; neither needs a calendar.

---

## Task 1: The images

Two Dockerfiles and their ignore files. Nothing runs yet; the deliverable is that both images build and the API image can execute the CLI.

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `frontend/Dockerfile`, `frontend/.dockerignore`

- [x] **Step 1: Write `backend/.dockerignore`**

```
tests/
.pytest_cache/
.mypy_cache/
.ruff_cache/
__pycache__/
*.pyc
.venv/
compose.test.yaml
```

- [x] **Step 2: Write `backend/Dockerfile`**

```dockerfile
# The API, and the backup tooling that shares its code (I5).
FROM python:3.12-slim

# `postgresql-client` is here because of I5: the restore drill has to run
# `pg_restore` AND import `podvinsya.domain` to fold the restored log, so
# the tools and the domain must live in one image. `--no-install-recommends`
# keeps that to the client binaries rather than a server.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source: the layer cache then survives every edit
# that does not touch pyproject.toml.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

COPY src/ ./src/
COPY alembic.ini ./
RUN pip install --no-cache-dir --no-deps .

# Unprivileged: nothing here writes to the image, and the backup volume is
# mounted with this uid's ownership by compose.
RUN useradd --create-home --uid 10001 budge
USER budge

# No CMD that migrates. §10 and I8 make migration a separate step, and an
# image that migrated on start would make that impossible to honour.
CMD ["podvinsya", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

- [x] **Step 3: Verify the API image builds and the CLI runs**

```bash
docker build -t budge-api:dev backend/; echo "exit=$?"
docker run --rm budge-api:dev podvinsya --help; echo "exit=$?"
docker run --rm budge-api:dev pg_restore --version; echo "exit=$?"
```

Expected: all 0, the help text lists `migrate`, `serve`, `hash-password` and `export-types`, and `pg_restore` prints a version. If `pip install .` fails because `pyproject.toml` references files not yet copied, move the `COPY src/` line above the first install and drop the second — report which you did.

- [x] **Step 4: Write `frontend/.dockerignore`**

```
node_modules/
dist/
```

- [x] **Step 5: Write `frontend/Dockerfile`**

The output is static files. The final stage is Caddy itself, so compose has one image to run for the web tier rather than a builder plus a volume dance.

```dockerfile
# Build the SPA, then hand it to Caddy. Two stages, one image: Caddy needs
# the files and nothing needs node at runtime.
FROM node:22-alpine AS build

RUN corepack enable
WORKDIR /app

# The lockfile alone first, so `pnpm install` re-runs only when it changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY ../Caddyfile /etc/caddy/Caddyfile
```

`COPY ../Caddyfile` cannot work — a Dockerfile may not read above its build context. Leave the last line **out** of this file; compose supplies the Caddyfile as a bind mount instead (Task 2), which also means editing it does not require a rebuild. Delete that line before building.

- [x] **Step 6: Verify the frontend image builds and holds the built SPA**

```bash
docker build -t budge-web:dev frontend/; echo "exit=$?"
docker run --rm budge-web:dev ls /srv; echo "exit=$?"
docker run --rm budge-web:dev sh -c 'ls /srv/assets | head'; echo "exit=$?"
```

Expected: exit 0, `/srv` contains `index.html` and an `assets/` directory, and `assets/` lists the hashed chunks including a `stage._token-*.js` and a `host.match._matchId-*.js`. If `pnpm-workspace.yaml` does not exist, drop it from the COPY line and say so.

- [x] **Step 7: Commit**

```bash
git add backend/Dockerfile backend/.dockerignore frontend/Dockerfile frontend/.dockerignore
git commit -m "build: images for the API and the static front end"
```

---

## Task 2: The stack

Compose, Caddy, and the environment contract. After this the whole thing runs.

**Files:**
- Create: `compose.yaml`, `Caddyfile`, `.env.example`
- Modify: `.gitignore` (ignore `.env`)

- [ ] **Step 1: Write `Caddyfile`**

```caddyfile
# §1.1 puts this on an isolated LAN with no TLS, and the session cookie is
# deliberately not `Secure`. So: plain HTTP, and automatic HTTPS off (I7).
# Left on, Caddy would try to provision a certificate for a name that has
# no public DNS, and serve nothing while it retried.
{
	auto_https off
	admin off
}

http://:80 {
	# One origin for everything, which is what the front end assumes: every
	# URL it builds is relative (§7.4, and the stage plan's endpoints.ts).
	handle /api/* {
		reverse_proxy api:8000
	}

	# The stage screen and the host console both hold their sockets here.
	# `reverse_proxy` upgrades WebSocket connections without extra config
	# in Caddy 2; the matcher is what routes them.
	handle /ws/* {
		reverse_proxy api:8000
	}

	handle /health {
		reverse_proxy api:8000
	}

	handle {
		root * /srv
		encode gzip
		# The SPA owns its routes: /stage/:token and /host/... exist only in
		# the browser, so anything that is not a file falls through to
		# index.html rather than 404ing on a reload.
		try_files {path} /index.html
		file_server
	}
}
```

- [ ] **Step 2: Write `.env.example`**

```bash
# Copy to `.env` and fill in. `.env` is not committed.

# --- database -------------------------------------------------------------
POSTGRES_USER=budge
POSTGRES_PASSWORD=change-me
POSTGRES_DB=budge

# --- object store (§10) ---------------------------------------------------
MINIO_ROOT_USER=budge
MINIO_ROOT_PASSWORD=change-me-too

# --- application ----------------------------------------------------------
# Any long random string. `openssl rand -hex 32` will do.
# Changing it invalidates every session cookie and every stage link.
PODVINSYA_SECRET_KEY=

# The output of:  echo -n 'the password' | docker run --rm -i budge-api podvinsya hash-password
PODVINSYA_HOST_PASSWORD=

# --- backups (§10) --------------------------------------------------------
# Seconds between backups, and between restore drills. One hour and one day.
BUDGE_BACKUP_INTERVAL=3600
BUDGE_DRILL_INTERVAL=86400
```

Add `.env` to `.gitignore`.

- [ ] **Step 3: Write `compose.yaml`**

```yaml
# The whole deployment (§10). One file, one `docker compose up -d`.
#
# Service order is not decoration: `migrate` runs to completion before
# `api` starts (§10, I8), and `api` waits for both stores to be healthy
# before that.

name: budge

x-postgres-url: &postgres-url
  PODVINSYA_DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}

x-s3: &s3
  PODVINSYA_S3_ENDPOINT: http://minio:9000
  PODVINSYA_S3_ACCESS_KEY: ${MINIO_ROOT_USER}
  PODVINSYA_S3_SECRET_KEY: ${MINIO_ROOT_PASSWORD}
  PODVINSYA_S3_BUCKET: budge-media

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 20

  minio:
    image: minio/minio:latest
    restart: unless-stopped
    command: server /data --address :9000
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 20

  # §10: «Миграции применяются отдельным шагом до старта приложения».
  # One-shot, and `api` will not start until it has exited 0.
  migrate:
    image: budge-api
    build: ./backend
    restart: "no"
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      <<: *postgres-url
    command: ["podvinsya", "migrate"]

  api:
    image: budge-api
    build: ./backend
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
      minio:
        condition: service_healthy
    environment:
      <<: [*postgres-url, *s3]
      PODVINSYA_SECRET_KEY: ${PODVINSYA_SECRET_KEY}
      PODVINSYA_HOST_PASSWORD: ${PODVINSYA_HOST_PASSWORD}
    healthcheck:
      # §10's own healthcheck endpoint, which already probes both stores.
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)\""]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 15s

  web:
    image: budge-web
    build: ./frontend
    restart: unless-stopped
    depends_on:
      - api
    ports:
      - "${BUDGE_PORT:-8080}:80"
    volumes:
      # Bind-mounted rather than baked in, so the operator can change a
      # route without rebuilding the SPA.
      - ./Caddyfile:/etc/caddy/Caddyfile:ro

  # §10: «бэкапы по расписанию с учениями по восстановлению» (I9).
  backup:
    image: budge-api
    build: ./backend
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
    environment:
      <<: [*postgres-url, *s3]
      BUDGE_BACKUP_INTERVAL: ${BUDGE_BACKUP_INTERVAL:-3600}
      BUDGE_DRILL_INTERVAL: ${BUDGE_DRILL_INTERVAL:-86400}
    volumes:
      - backups:/backups
    entrypoint: ["/bin/sh", "/app/scripts/backup-loop.sh"]

volumes:
  postgres-data:
  minio-data:
  backups:
```

The `backup` service references `/app/scripts/backup-loop.sh`, which Task 5 writes. Until then, comment the whole `backup:` service out — a compose file that cannot come up is not a deliverable. Uncomment it in Task 5.

- [ ] **Step 4: Bring the stack up and prove it serves**

```bash
cp .env.example .env
# Fill in the two secrets the app refuses to start without.
sed -i "s|^PODVINSYA_SECRET_KEY=.*|PODVINSYA_SECRET_KEY=$(openssl rand -hex 32)|" .env
docker compose build; echo "exit=$?"
docker compose run --rm --no-deps api sh -c 'echo -n smoke-test-password | podvinsya hash-password'
```

Put that hash into `.env` as `PODVINSYA_HOST_PASSWORD`, then:

```bash
docker compose up -d; echo "exit=$?"
docker compose ps
```

Expected: `migrate` shows `exited (0)`, everything else `running`, and `api` reaching `healthy`. Then:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/health
curl -sS http://127.0.0.1:8080/health
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/host
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/stage/nonsense
```

Expected: `/health` is `200` with `{"status":"ok","checks":{"database":true,"storage":true}}`; `/host` and `/stage/nonsense` both `200` and serve `index.html` (the SPA owns those routes — the 404 for a bad token happens on the socket, not here).

- [ ] **Step 5: Prove the login path works end to end**

```bash
curl -sS -i -X POST http://127.0.0.1:8080/api/session \
  -H 'content-type: application/json' \
  -d '{"password":"smoke-test-password"}' | head -20
```

Expected: `HTTP/1.1 204`, and a `set-cookie` header **without** the `Secure` attribute (I7 and §1.1 — with it, the operator could never log in over plain HTTP). Then confirm a wrong password is refused:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/session \
  -H 'content-type: application/json' -d '{"password":"wrong"}'
```

Expected: `401`.

- [ ] **Step 6: Prove migrations really are a separate step**

```bash
docker compose down
docker volume rm budge_postgres-data
docker compose up -d --no-deps postgres minio
docker compose up -d api 2>&1 | tail -5; echo "exit=$?"
```

Expected: compose starts `migrate` first because `api` depends on it, and `api` only starts after `migrate` exits 0. Confirm with `docker compose logs migrate` showing Alembic running, and `docker compose logs api` showing no migration output at all. If `api` ever migrates, I8 is broken — fix it and say what you found.

- [ ] **Step 7: Tear down and commit**

```bash
docker compose down -v
git add compose.yaml Caddyfile .env.example .gitignore
git commit -m "build: the deployable stack, with migrations as their own step"
```

- [ ] **Step 8: Confirm `.env` is not staged**

```bash
git status --porcelain | grep -F '.env' || echo "ok: .env is ignored"
```

Expected: `ok: .env is ignored`. If `.env` appears, the `.gitignore` entry is wrong — fix it before moving on. A committed `.env` is a leaked signing key.

---

## Task 3: Taking a backup

`podvinsya backup --to DIR`: a `pg_dump` archive, a manifest of the digests that dump references, and the media mirror brought up to date.

**Files:**
- Create: `backend/src/podvinsya/backup/__init__.py`, `paths.py`, `dump.py`
- Create: `backend/tests/backup/__init__.py`, `conftest.py`, `test_paths.py`, `test_dump.py`
- Modify: `backend/src/podvinsya/cli.py`

**Interfaces:**
- Produces:
  - `BackupRoot(path)` with `.dumps`, `.media`, `.drills`, `.dump_for(stamp)`, `.manifest_for(stamp)`, `.blob(digest)`, `.prepare()`, `.stamps()`, `.newest()`
  - `stamp(moment) -> str`, `STAMP_FORMAT`
  - `Manifest(taken_at, revision, digests)` with `.write(path)` / `Manifest.read(path)`
  - `async take(root, *, database_url, media) -> Manifest`

- [ ] **Step 1: Write the failing paths test — `backend/tests/backup/test_paths.py`**

```python
"""Where a backup lives.

Nothing here touches a database. `BackupRoot` is pure path arithmetic, and
it is tested on its own because one of its methods takes a value that
arrives from a database column — and a path built from untrusted text is
how a backup directory becomes a way to write anywhere on the disk.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podvinsya.backup.paths import BackupRoot, Manifest, stamp

DIGEST = "a" * 64


def test_it_stamps_in_utc_whatever_zone_it_is_handed() -> None:
    """Kills on: using the local zone. Two backups an hour apart would sort
    out of order across a DST boundary, and `newest()` would restore the
    wrong one."""
    moment = datetime(2026, 8, 23, 20, 0, 0, tzinfo=timezone.utc)
    assert stamp(moment) == "20260823T200000Z"


def test_a_stamp_sorts_chronologically_as_text() -> None:
    """`newest()` sorts strings, so the format has to make that correct."""
    earlier = stamp(datetime(2026, 8, 23, 9, 0, 0, tzinfo=timezone.utc))
    later = stamp(datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc))
    assert earlier < later


def test_it_shards_a_blob_by_its_first_byte(tmp_path: Path) -> None:
    """One directory with a hundred thousand entries is slow to list and
    slower to sync. Two hex characters is 256 buckets, which is enough."""
    root = BackupRoot(tmp_path)
    assert root.blob(DIGEST) == tmp_path / "media" / "aa" / DIGEST


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "a" * 63, "a" * 65, "A" * 64, "../" + "a" * 61, "", "a/b"],
)
def test_it_refuses_anything_that_is_not_a_digest(tmp_path: Path, hostile: str) -> None:
    """The value reaches here from `images.media_sha256`, and the column's
    check constraint is the only thing that has ever validated it.

    Kills on: joining the value straight onto the path — a row whose digest
    read `../../etc/passwd` would make a backup run write outside its own
    directory, and the drill read outside it.
    """
    with pytest.raises(ValueError):
        BackupRoot(tmp_path).blob(hostile)


def test_prepare_makes_every_directory_a_backup_writes_into(tmp_path: Path) -> None:
    root = BackupRoot(tmp_path)
    root.prepare()
    assert root.dumps.is_dir()
    assert root.media.is_dir()
    assert root.drills.is_dir()


def test_prepare_is_safe_to_run_against_an_existing_root(tmp_path: Path) -> None:
    """Every backup run calls it. Kills on: `mkdir` without `exist_ok`,
    which would make the second backup the last one."""
    root = BackupRoot(tmp_path)
    root.prepare()
    root.prepare()
    assert root.dumps.is_dir()


def test_newest_is_none_before_anything_has_been_taken(tmp_path: Path) -> None:
    """Kills on: raising. The drill runs on a schedule and will meet an
    empty root on the first day; that is not an error, it is Tuesday."""
    root = BackupRoot(tmp_path)
    root.prepare()
    assert root.newest() is None


def test_newest_ignores_a_dump_with_no_manifest(tmp_path: Path) -> None:
    """A dump written but not yet manifested is a backup interrupted
    mid-run. Kills on: returning it — the drill would restore an archive
    whose digest list it cannot check, and report a pass it did not earn."""
    root = BackupRoot(tmp_path)
    root.prepare()
    root.dump_for("20260823T090000Z").write_bytes(b"archive")
    Manifest(taken_at="20260823T080000Z", revision="0002", digests=(DIGEST,)).write(
        root.manifest_for("20260823T080000Z")
    )
    root.dump_for("20260823T080000Z").write_bytes(b"archive")
    assert root.newest() == "20260823T080000Z"


def test_a_manifest_round_trips(tmp_path: Path) -> None:
    written = Manifest(taken_at="20260823T080000Z", revision="0002", digests=(DIGEST, "b" * 64))
    written.write(tmp_path / "m.json")
    assert Manifest.read(tmp_path / "m.json") == written
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd backend && pytest tests/backup/test_paths.py -q; echo "exit=$?"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'podvinsya.backup'`.

- [ ] **Step 3: Write `backend/src/podvinsya/backup/__init__.py`**

```python
"""§10's «бэкапы по расписанию с учениями по восстановлению».

Two commands, not one. `dump` takes a backup; `drill` proves one can be
restored. The second exists because the first is not evidence: an archive
that `pg_dump` wrote and nobody ever read back is a file, not a backup.

Both live in the application package rather than in a shell script for the
reason ruling I2 gives — the drill folds the restored log through the
domain's own `fold`, so it has to be able to import it.
"""
```

- [ ] **Step 4: Write `backend/src/podvinsya/backup/paths.py`**

```python
"""Where a backup lives, and how it is named.

Layout:

    <root>/dumps/20260823T200000Z.dump    pg_dump --format=custom
    <root>/dumps/20260823T200000Z.json    the manifest for that dump
    <root>/media/aa/aaaa…                 the shared blob mirror (I6)
    <root>/drills/20260823T210000Z.json   what a drill found

The mirror is shared across dumps and never pruned: a digest names its own
bytes (§7.6) so a blob never changes, §5.3 deletes content only softly, and
an old dump has to stay restorable.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# Fixed-width, UTC, and lexicographically chronological — which is what
# lets `newest()` sort strings instead of parsing every name.
_STAMP = re.compile(r"^\d{8}T\d{6}Z$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(STAMP_FORMAT)


@dataclass(frozen=True)
class Manifest:
    """What one dump references.

    `digests` is what makes the drill's media cross-check exact (I3): it is
    the set of blobs the restored database will ask for, recorded at the
    moment the dump was taken rather than inferred afterwards.
    """

    taken_at: str
    revision: str | None
    digests: tuple[str, ...]

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "taken_at": self.taken_at,
                    "revision": self.revision,
                    "digests": list(self.digests),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            taken_at=loaded["taken_at"],
            revision=loaded["revision"],
            digests=tuple(loaded["digests"]),
        )


@dataclass(frozen=True)
class BackupRoot:
    path: Path

    @property
    def dumps(self) -> Path:
        return self.path / "dumps"

    @property
    def media(self) -> Path:
        return self.path / "media"

    @property
    def drills(self) -> Path:
        return self.path / "drills"

    def dump_for(self, moment: str) -> Path:
        return self.dumps / f"{moment}.dump"

    def manifest_for(self, moment: str) -> Path:
        return self.dumps / f"{moment}.json"

    def blob(self, digest: str) -> Path:
        """The mirror path for one blob.

        The digest arrives from `images.media_sha256`, and the column's
        check constraint is the only thing that has ever validated it. This
        re-checks rather than trusting: a path built by joining untrusted
        text is how a backup directory becomes a way to write anywhere.
        """
        if not _DIGEST.match(digest):
            raise ValueError(f"not a sha256 digest: {digest!r}")
        return self.media / digest[:2] / digest

    def prepare(self) -> None:
        for directory in (self.dumps, self.media, self.drills):
            directory.mkdir(parents=True, exist_ok=True)

    def stamps(self) -> tuple[str, ...]:
        """Every backup that is complete — archive *and* manifest.

        A dump without its manifest is a run interrupted between the two
        writes. Reporting it would let the drill restore an archive whose
        digest list it cannot check.
        """
        if not self.dumps.is_dir():
            return ()
        found = [
            path.stem
            for path in self.dumps.glob("*.dump")
            if _STAMP.match(path.stem) and self.manifest_for(path.stem).is_file()
        ]
        return tuple(sorted(found))

    def newest(self) -> str | None:
        found = self.stamps()
        return found[-1] if found else None
```

- [ ] **Step 5: Run it and watch it pass**

```bash
cd backend && pytest tests/backup/test_paths.py -q; echo "exit=$?"
```

Expected: PASS, 14 tests (the hostile-digest case is parametrised seven ways).

- [ ] **Step 6: Write `backend/tests/backup/conftest.py`**

The backup suite needs a real database — `pg_dump` against a mock proves nothing. It builds its own engine rather than importing `tests/db`'s, because that conftest's collection hook is scoped to its own directory.

```python
"""Fixtures for the backup suite.

A real PostgreSQL, because the thing under test is `pg_dump` and
`pg_restore`. Every module here must carry both
`pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]`
— asyncpg binds a connection to the loop it was made on, and the engine
below is session-scoped.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from podvinsya.db.engine import create_engine, sessionmaker_for
from support.db import DATABASE_URL, alembic_config

UNREACHABLE = (
    f"Cannot reach the test database at {DATABASE_URL}.\n"
    "Start it with:  docker compose -f backend/compose.test.yaml up -d\n"
    "These tests fail rather than skip: a silently skipped backup suite "
    "reports green while proving nothing — which is the exact failure the "
    "restore drill exists to prevent."
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_engine(DATABASE_URL)
    try:
        async with eng.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # re-raised as a usable message
        await eng.dispose()
        pytest.fail(f"{UNREACHABLE}\n\nunderlying error: {exc!r}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await asyncio.to_thread(command.upgrade, alembic_config(DATABASE_URL), "head")


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(migrated_schema: None, engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE match_events, match_players, matches, images, categories "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def sessions(
    migrated_schema: None, engine: AsyncEngine
) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)
```

Also create an empty `backend/tests/backup/__init__.py`.

- [ ] **Step 7: Write the failing dump test — `backend/tests/backup/test_dump.py`**

```python
"""Taking a backup, against a real database and a real store."""

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.backup.dump import take
from podvinsya.backup.paths import BackupRoot, Manifest
from support.db import DATABASE_URL
from support.media import InMemoryMediaStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _stock(sessions: async_sessionmaker[AsyncSession], digest: str) -> None:
    """One category and one image, which is the smallest thing a manifest
    can be non-empty about."""
    category = uuid4()
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, title, is_secret, is_active, version) "
                "VALUES (:id, 'Кино', false, true, 1)"
            ),
            {"id": category},
        )
        await session.execute(
            text(
                "INSERT INTO images (id, category_id, media_sha256, answer_text, "
                "position, is_active) VALUES (:id, :category, :digest, 'Титаник', 0, true)"
            ),
            {"id": uuid4(), "category": category, "digest": digest},
        )
        await session.commit()


async def test_it_writes_an_archive_pg_restore_recognises(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I4: custom format, not SQL text.

    Kills on: `--format=plain`. A text dump replays statement by statement,
    so a truncated one restores its first half and leaves a database that
    looks plausible; `pg_restore` refuses a custom archive it cannot read
    whole.
    """
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    archive = root.dump_for(manifest.taken_at)
    assert archive.is_file()
    listed = subprocess.run(
        ["pg_restore", "--list", str(archive)], capture_output=True, text=True
    )
    assert listed.returncode == 0, listed.stderr
    assert "match_events" in listed.stdout


async def test_the_manifest_names_every_digest_the_dump_references(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I3's cross-check is only exact because this list is recorded at the
    moment the dump is taken.

    Kills on: an empty or inferred digest list — the drill would report a
    pass on a backup whose pictures were never copied.
    """
    digest = "c" * 64
    await _stock(sessions, digest)
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert digest in manifest.digests


async def test_it_copies_the_bytes_behind_every_digest(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Kills on: writing the manifest and not the blobs, which is the
    asymmetric failure I3 exists to catch — and which would otherwise be
    discovered on the night of a restore."""
    digest = "d" * 64
    await _stock(sessions, digest)
    store = InMemoryMediaStore()
    store.objects[digest] = b"a picture"

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=store)
    assert root.blob(digest).read_bytes() == b"a picture"


async def test_it_does_not_refetch_a_blob_it_already_mirrored(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I6: a digest names its bytes, so a mirrored blob is final.

    Kills on: re-downloading the whole library every run, which turns an
    hourly backup into an hourly transfer of everything ever uploaded.
    """
    digest = "e" * 64
    await _stock(sessions, digest)
    store = InMemoryMediaStore()
    store.objects[digest] = b"a picture"

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=store)
    first = store.gets
    await take(root, database_url=DATABASE_URL, media=store)
    assert store.gets == first


async def test_a_blob_the_store_has_lost_does_not_stop_the_backup(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The row is there and the object is not — which is exactly the state
    a backup most needs to record.

    Kills on: raising. The run would abort, no archive would be written,
    and the one night the store had a hole would also be the night with no
    backup at all. It belongs in the manifest so the drill reports it.
    """
    digest = "f" * 64
    await _stock(sessions, digest)
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert digest in manifest.digests
    assert not root.blob(digest).exists()


async def test_it_records_the_schema_revision(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """So a drill can say "restored, and at the revision this code expects"
    rather than only "restored"."""
    root = BackupRoot(tmp_path)
    manifest = await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert manifest.revision is not None
    assert Manifest.read(root.manifest_for(manifest.taken_at)) == manifest
```

`InMemoryMediaStore` at `backend/tests/support/media.py` already holds its bytes in `self.objects` and counts `self.puts`. It has no read counter, which `test_it_does_not_refetch_a_blob_it_already_mirrored` needs. Add one and nothing else — leave the `fail` switch, `puts`, and every method's behaviour exactly as they are:

```python
    def __init__(self, *, fail: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail = fail
        self.puts = 0
        # Reads are counted for the backup suite: I6 says a mirrored blob
        # is never fetched twice, and only a counter can show that.
        self.gets = 0

    async def get(self, digest: str) -> bytes | None:
        self._check()
        self.gets += 1
        return self.objects.get(digest)
```

- [ ] **Step 8: Run it and watch it fail**

```bash
cd backend && pytest tests/backup/test_dump.py -q; echo "exit=$?"
```

Expected: FAIL — no module `podvinsya.backup.dump`.

- [ ] **Step 9: Write `backend/src/podvinsya/backup/dump.py`**

```python
"""Taking a backup: the database, and the blobs it points at.

The two halves are taken in this order deliberately. The dump is a
consistent snapshot of the rows; the media mirror is then brought up to
date against *that* snapshot's digest list. A blob uploaded after the dump
is simply not in it, which is correct — the dump does not reference it
either. The reverse order would leave the manifest naming a row the
archive does not contain.
"""

import asyncio
import subprocess
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url

from podvinsya.backup.paths import BackupRoot, Manifest, stamp
from podvinsya.db.engine import create_engine
from podvinsya.services.ports import MediaStore


class BackupFailed(Exception):
    """`pg_dump` refused. Deliberately not caught anywhere below: a backup
    that failed must fail loudly, and the scheduler's job is to report it,
    not to carry on."""


def libpq_url(database_url: str) -> str:
    """`postgresql+asyncpg://…` is a SQLAlchemy URL; `pg_dump` speaks
    libpq. Same host, same credentials, different scheme."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _pg_dump(database_url: str, into: str) -> None:
    # `--format=custom` is I4. `--no-owner` and `--no-privileges` keep the
    # archive restorable into a scratch database owned by whoever is
    # drilling, which is not necessarily the production role.
    finished = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            into,
            libpq_url(database_url),
        ],
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        raise BackupFailed(f"pg_dump exited {finished.returncode}: {finished.stderr.strip()}")


async def take(root: BackupRoot, *, database_url: str, media: MediaStore) -> Manifest:
    """Take one backup. Returns the manifest it wrote."""
    root.prepare()
    moment = stamp(datetime.now(timezone.utc))

    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            digests = tuple(
                sorted(
                    row[0]
                    for row in (
                        await connection.execute(text("SELECT DISTINCT media_sha256 FROM images"))
                    ).all()
                )
            )
    finally:
        await engine.dispose()

    # Off the loop: `pg_dump` is a blocking subprocess, and the scheduler
    # container runs it beside nothing else — but the API's event loop is
    # one import away and this must never be the thing that stalls it.
    await asyncio.to_thread(_pg_dump, database_url, str(root.dump_for(moment)))

    for digest in digests:
        destination = root.blob(digest)
        # I6: a digest names its bytes, so a mirrored blob is final.
        if destination.exists():
            continue
        data = await media.get(digest)
        if data is None:
            # The row points at an object the store has lost. Recorded in
            # the manifest and left for the drill to report — aborting here
            # would mean no backup at all on the one night there was a hole.
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    manifest = Manifest(taken_at=moment, revision=revision, digests=digests)
    # Written last, and this is what `stamps()` keys on: a run interrupted
    # before this line leaves an archive the drill will not pick up.
    manifest.write(root.manifest_for(moment))
    return manifest
```

If `MediaStore` is not importable from `podvinsya.services.ports`, find where the protocol lives and import it from there — do not redeclare it.

- [ ] **Step 10: Run it and watch it pass**

```bash
cd backend && pytest tests/backup/ -q; echo "exit=$?"
```

Expected: PASS, all of `test_paths.py` plus 6 from `test_dump.py`.

- [ ] **Step 11: Add the CLI subcommand**

In `backend/src/podvinsya/cli.py`, alongside the existing subparsers:

```python
    backup = subcommands.add_parser("backup", help="take a backup (§10)")
    backup.add_argument("--to", default="/backups", help="the backup root directory")
```

and in the dispatch chain, following the lazy-import convention the `serve` branch already uses and explains:

```python
    if args.command == "backup":
        # Imported here for the reason `serve`'s imports are: `migrate`
        # must not pull the media stack in to run one Alembic command.
        import asyncio
        from pathlib import Path

        from podvinsya.api.settings import ApiSettings
        from podvinsya.backup.dump import take
        from podvinsya.backup.paths import BackupRoot
        from podvinsya.media.s3 import S3MediaStore

        settings = ApiSettings()
        store = S3MediaStore(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )
        manifest = asyncio.run(
            take(BackupRoot(Path(args.to)), database_url=settings.database_url, media=store)
        )
        print(f"{manifest.taken_at}: {len(manifest.digests)} media referenced")
        return 0
```

Match `S3MediaStore`'s actual constructor signature — read it rather than assuming these keyword names, and report what it turned out to be.

- [ ] **Step 12: Test the CLI wiring — append to `backend/tests/test_cli.py`**

Follow the file's existing `serve_environment()` helper convention.

```python
def test_backup_needs_the_media_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills on: constructing `Settings` rather than `ApiSettings` — the
    backup would start, dump the database, and silently mirror nothing,
    because it would have no store to read from."""
    for name in ("PODVINSYA_S3_ENDPOINT", "PODVINSYA_S3_ACCESS_KEY", "PODVINSYA_S3_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PODVINSYA_DATABASE_URL", "postgresql+asyncpg://x:y@127.0.0.1:1/z")
    with pytest.raises(ValidationError):
        main(["backup", "--to", "/tmp/does-not-matter"])
```

- [ ] **Step 13: Green the whole backend suite and commit**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd .. && git add backend && git commit -m "feat(backup): take a dump, and mirror the pictures it names"
```

---

## Task 4: The restore drill

The half of §10 that makes the other half mean something. `podvinsya restore-drill --from DIR` restores the newest backup into a scratch database and proves the history in it still folds.

**Files:**
- Create: `backend/src/podvinsya/backup/scratch.py`, `drill.py`
- Create: `backend/tests/backup/test_drill.py`
- Modify: `backend/src/podvinsya/cli.py`

**Interfaces:**
- Produces:
  - `scratch_database(database_url, name)` — async context manager yielding the scratch URL, dropping it on the way out however the body ended
  - `DrillReport(dump, drilled_at, scratch, revision, matches, events, missing_media, failures)` with `.passed`
  - `async run(root, *, database_url, expected_revision=None) -> DrillReport`

- [ ] **Step 1: Write the failing drill test — `backend/tests/backup/test_drill.py`**

```python
"""The drill. Every test here restores a real archive into a real database.

That is the point: a drill tested against a mock is the thing it exists to
prevent.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podvinsya.backup import drill
from podvinsya.backup.dump import take
from podvinsya.backup.paths import BackupRoot, Manifest
from podvinsya.db.engine import create_engine
from podvinsya.db.repository import MatchRepository
from podvinsya.db.store import UnitOfWork
from podvinsya.domain.events import MatchCreated
from podvinsya.domain.ids import MatchId
from support.db import DATABASE_URL
from support.media import InMemoryMediaStore
from support.streams import build_rich_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _a_match(sessions: async_sessionmaker[AsyncSession]) -> MatchId:
    """A match with a real, multi-event log.

    `build_rich_stream()` is the suite's existing generator of a legal
    stream (`tests/support/streams.py`); the genesis goes in through
    `MatchRepository.create` and the rest through a `UnitOfWork`, which is
    exactly what `tests/db/test_store.py` does. A drill that folded a
    genesis-only log would prove almost nothing — I2 is about a *history*
    still evolving.
    """
    recorded = build_rich_stream()
    match_id = recorded.state.id
    created = recorded.events[0]
    assert isinstance(created, MatchCreated)
    await MatchRepository(sessions).create(match_id, created, operation_id="op-create")
    rest = recorded.events[1:]
    if rest:
        async with UnitOfWork(sessions).begin() as tx:
            await tx.append(match_id, expected_last_seq=1, events=rest, operation_id="op-rest")
    return match_id


async def test_it_reports_a_pass_on_a_backup_it_just_took(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    await _a_match(sessions)
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    report = await drill.run(root, database_url=DATABASE_URL)
    assert report.passed, report.failures
    assert report.matches == 1
    assert report.events >= 1


async def test_it_never_touches_the_live_database(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I1, and the one property in this plan that must be impossible rather
    than unlikely.

    Kills on: restoring into the configured database — a scheduled job
    would then wipe production on a timer, replacing whatever happened
    since the last backup with the backup.
    """
    match_id = await _a_match(sessions)
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    # A row that exists ONLY after the backup was taken. A drill that
    # restored over the live database would delete it.
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, title, is_secret, is_active, version) "
                "VALUES (:id, 'После бэкапа', false, true, 1)"
            ),
            {"id": uuid4()},
        )
        await session.commit()

    await drill.run(root, database_url=DATABASE_URL)

    async with sessions() as session:
        survived = (
            await session.execute(
                text("SELECT count(*) FROM categories WHERE title = 'После бэкапа'")
            )
        ).scalar_one()
        kept = (
            await session.execute(
                text("SELECT count(*) FROM matches WHERE id = :id"), {"id": match_id}
            )
        ).scalar_one()
    assert survived == 1, "the drill deleted a row written after the backup"
    assert kept == 1


async def test_it_drops_the_scratch_database_afterwards(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Kills on: leaving it behind. An hourly drill would fill the disk
    with restored copies, and the first symptom would be production
    running out of space."""
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    report = await drill.run(root, database_url=DATABASE_URL)

    engine = create_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            remaining = (
                await connection.execute(
                    text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                    {"name": report.scratch},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert remaining == 0


async def test_it_drops_the_scratch_database_even_when_the_drill_fails(
    clean_db: None, tmp_path: Path
) -> None:
    """The failing path is the one that leaks. Kills on: a drop that only
    runs on success."""
    root = BackupRoot(tmp_path)
    root.prepare()
    # An archive pg_restore cannot read: the scratch database is created,
    # then the restore fails.
    root.dump_for("20260823T080000Z").write_bytes(b"not an archive")
    Manifest(taken_at="20260823T080000Z", revision="0002", digests=()).write(
        root.manifest_for("20260823T080000Z")
    )

    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed

    engine = create_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            remaining = (
                await connection.execute(
                    text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                    {"name": report.scratch},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert remaining == 0


async def test_it_fails_when_a_referenced_picture_was_never_mirrored(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I3: the asymmetric failure — rows saved, blobs missed.

    Kills on: checking only the database. The restore would report a clean
    pass, and the game would come back with every picture missing.
    """
    category = uuid4()
    digest = "b" * 64
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, title, is_secret, is_active, version) "
                "VALUES (:id, 'Кино', false, true, 1)"
            ),
            {"id": category},
        )
        await session.execute(
            text(
                "INSERT INTO images (id, category_id, media_sha256, answer_text, "
                "position, is_active) VALUES (:id, :category, :digest, 'Титаник', 0, true)"
            ),
            {"id": uuid4(), "category": category, "digest": digest},
        )
        await session.commit()

    root = BackupRoot(tmp_path)
    # The store has lost the object, so `take` mirrors nothing for it.
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed
    assert digest in report.missing_media


async def test_it_fails_when_a_restored_log_no_longer_folds(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """I2, and the reason this lives in the application rather than in a
    shell script.

    A log whose first event is not `MatchCreated` decodes fine and restores
    fine — and cannot be recovered from. Kills on: proving only that
    `pg_restore` exited 0, which is the weakest property available and the
    one a shell script would check.
    """
    await _a_match(sessions)
    async with sessions() as session:
        await session.execute(text("UPDATE match_events SET type = 'PlayerAdded' WHERE seq = 1"))
        await session.commit()

    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())

    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed
    assert report.failures


async def test_it_reports_nothing_to_drill_on_an_empty_root(tmp_path: Path) -> None:
    """First run of a fresh deployment. Kills on: raising — the scheduler
    would log a crash every day until the first backup landed."""
    root = BackupRoot(tmp_path)
    root.prepare()
    report = await drill.run(root, database_url=DATABASE_URL)
    assert not report.passed
    assert report.dump is None


async def test_it_writes_its_report_where_an_operator_will_find_it(
    clean_db: None, tmp_path: Path
) -> None:
    """A drill nobody can read the result of is a drill nobody ran."""
    root = BackupRoot(tmp_path)
    await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    report = await drill.run(root, database_url=DATABASE_URL)
    written = list(root.drills.glob("*.json"))
    assert len(written) == 1
    assert report.drilled_at in written[0].name
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd backend && pytest tests/backup/test_drill.py -q; echo "exit=$?"
```

Expected: FAIL — no module `podvinsya.backup.drill`.

- [ ] **Step 3: Write `backend/src/podvinsya/backup/scratch.py`**

```python
"""The scratch database a drill restores into.

I1: created for the drill, dropped when it ends, and never the configured
database. `drill.py` gets its scratch URL from here and has no other way to
name a database — which is what keeps "restore over production" from being
one typo away.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url

from podvinsya.db.engine import create_engine

# Where `CREATE DATABASE` is issued from. A connection cannot create the
# database it is connected to, so this runs against the cluster's default
# maintenance database.
MAINTENANCE = "postgres"


def scratch_url(database_url: str, name: str) -> str:
    return make_url(database_url).set(database=name).render_as_string(hide_password=False)


def _maintenance_url(database_url: str) -> str:
    return scratch_url(database_url, MAINTENANCE)


@asynccontextmanager
async def scratch_database(database_url: str, name: str) -> AsyncIterator[str]:
    """Create `name`, yield a URL for it, and drop it on the way out —
    however the body ended.

    `CREATE DATABASE` and `DROP DATABASE` cannot run inside a transaction,
    hence AUTOCOMMIT. The identifier is quoted rather than interpolated:
    it is built from a timestamp here, but a helper that is only safe for
    its current caller is a trap for the next one.
    """
    engine = create_engine(_maintenance_url(database_url)).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    quoted = '"' + name.replace('"', '""') + '"'
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
            await connection.execute(text(f"CREATE DATABASE {quoted}"))
        try:
            yield scratch_url(database_url, name)
        finally:
            # WITH (FORCE) rather than a bare DROP: a connection the
            # restore left open would otherwise keep the database alive,
            # and the next drill would find it and fail.
            async with engine.connect() as connection:
                await connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
    finally:
        await engine.dispose()
```

- [ ] **Step 4: Write `backend/src/podvinsya/backup/drill.py`**

```python
"""§10's «учения по восстановлению».

What this proves, in order, and why each step is not the previous one:

1. `pg_restore` accepts the archive          — the file is whole (I4)
2. the restored schema is at a revision      — it is this application's schema
3. every match's log folds                   — the history is *usable* (I2)
4. every referenced digest was mirrored      — the pictures came too (I3)

Only the first of those is what a shell script would check, and it is the
weakest: an archive can restore perfectly into a database whose event log
this code can no longer replay.
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url

from podvinsya.backup.dump import libpq_url
from podvinsya.backup.paths import BackupRoot, Manifest, stamp
from podvinsya.backup.scratch import scratch_database
from podvinsya.db.engine import create_engine, sessionmaker_for
from podvinsya.db.repository import MatchRepository
from podvinsya.domain.ids import MatchId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrillReport:
    dump: str | None
    drilled_at: str
    scratch: str
    revision: str | None
    matches: int
    events: int
    missing_media: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.dump is not None and not self.failures and not self.missing_media


def _pg_restore(archive: str, into: str) -> None:
    finished = subprocess.run(
        ["pg_restore", "--no-owner", "--no-privileges", "--dbname", into, archive],
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        raise RuntimeError(f"pg_restore exited {finished.returncode}: {finished.stderr.strip()}")


async def _fold_every_match(scratch: str) -> tuple[int, int, tuple[str, ...]]:
    """I2. `MatchRepository.load` is the same path recovery uses: it reads
    the log, insists the first event is `MatchCreated`, and folds. Reusing
    it is what makes this a rehearsal rather than an imitation."""
    engine = create_engine(scratch)
    failures: list[str] = []
    matches = 0
    events = 0
    try:
        sessions = sessionmaker_for(engine)
        async with sessions() as session:
            ids = [
                row[0] for row in (await session.execute(text("SELECT id FROM matches"))).all()
            ]
        repository = MatchRepository(sessions)
        for raw in ids:
            matches += 1
            try:
                log = await repository.read_events(MatchId(raw))
                events += len(log)
                await repository.load(MatchId(raw))
            except Exception as failure:  # every failure is a finding, not a crash
                failures.append(f"{raw}: {type(failure).__name__}: {failure}")
    finally:
        await engine.dispose()
    return matches, events, tuple(failures)


async def _revision_of(scratch: str) -> str | None:
    engine = create_engine(scratch)
    try:
        async with engine.connect() as connection:
            return (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    finally:
        await engine.dispose()


async def run(
    root: BackupRoot, *, database_url: str, expected_revision: str | None = None
) -> DrillReport:
    """Restore the newest backup into a scratch database and prove it."""
    root.prepare()
    drilled_at = stamp(datetime.now(timezone.utc))
    newest = root.newest()

    if newest is None:
        report = DrillReport(
            dump=None,
            drilled_at=drilled_at,
            scratch="",
            revision=None,
            matches=0,
            events=0,
            missing_media=(),
            failures=("no backup to drill",),
        )
        _write(root, report)
        return report

    manifest = Manifest.read(root.manifest_for(newest))
    # I1: the name is derived here and nowhere else, and it can never be
    # the configured database — it always carries the drill suffix.
    live = make_url(database_url).database or "budge"
    name = f"{live}_drill_{drilled_at}"

    failures: list[str] = []
    revision: str | None = None
    matches = events = 0

    try:
        async with scratch_database(database_url, name) as scratch:
            await asyncio.to_thread(
                _pg_restore, str(root.dump_for(newest)), libpq_url(scratch)
            )
            revision = await _revision_of(scratch)
            if revision is None:
                failures.append("the restored database carries no alembic_version")
            elif expected_revision is not None and revision != expected_revision:
                failures.append(f"restored at revision {revision}, expected {expected_revision}")
            matches, events, fold_failures = await _fold_every_match(scratch)
            failures.extend(fold_failures)
    except Exception as failure:
        failures.append(f"{type(failure).__name__}: {failure}")

    # I3, checked against the manifest rather than against the restored
    # rows: the manifest is what the dump *said* it needed.
    missing = tuple(
        digest for digest in manifest.digests if not root.blob(digest).is_file()
    )

    report = DrillReport(
        dump=newest,
        drilled_at=drilled_at,
        scratch=name,
        revision=revision,
        matches=matches,
        events=events,
        missing_media=missing,
        failures=tuple(failures),
    )
    _write(root, report)
    if not report.passed:
        logger.error("restore drill failed: %s", report)
    return report


def _write(root: BackupRoot, report: DrillReport) -> None:
    payload = asdict(report) | {"passed": report.passed}
    (root.drills / f"{report.drilled_at}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
```

- [ ] **Step 5: Run it and watch it pass**

```bash
cd backend && pytest tests/backup/test_drill.py -q; echo "exit=$?"
```

Expected: PASS, 8 tests. The APIs `_a_match` uses were read from the tree, not guessed: `MatchRepository.create(match_id, created, operation_id=...)` takes an already-decided `MatchCreated`, and `UnitOfWork(sessions).begin()` yields the `tx` that `append(match_id, expected_last_seq=..., events=..., operation_id=...)` hangs off. If either has moved, follow `tests/db/test_store.py`, which uses both.

- [ ] **Step 6: Add the CLI subcommand**

```python
    drill_parser = subcommands.add_parser(
        "restore-drill", help="restore the newest backup into a scratch database and prove it (§10)"
    )
    drill_parser.add_argument("--from", dest="source", default="/backups")
```

and in the dispatch chain:

```python
    if args.command == "restore-drill":
        import asyncio
        from pathlib import Path

        from podvinsya.backup import drill
        from podvinsya.backup.paths import BackupRoot
        from podvinsya.config import Settings

        report = asyncio.run(
            drill.run(BackupRoot(Path(args.source)), database_url=Settings().database_url)
        )
        print(json.dumps({"passed": report.passed, "dump": report.dump}, ensure_ascii=False))
        # Non-zero on failure, so the scheduler and any human running this
        # by hand both learn the answer without reading a log.
        return 0 if report.passed else 1
```

with `import json` at module scope if it is not already there. Note this branch constructs `Settings`, not `ApiSettings`: the drill needs a database and nothing else, and demanding a signing key to rehearse a restore would be the same mistake ruling 11 already avoided for `migrate`.

- [ ] **Step 7: Green the whole backend suite and commit**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd .. && git add backend && git commit -m "feat(backup): rehearse a restore, and fold what comes back"
```

---

## Task 5: The schedule

§10 asks for backups «по расписанию с учениями по восстановлению». I9 puts the schedule in a container so the whole deployment is one directory and one `docker compose up`.

**Files:**
- Create: `backend/scripts/backup-loop.sh`
- Modify: `backend/Dockerfile` (copy the script), `compose.yaml` (uncomment `backup`)
- Create: `docs/operations.md`

- [ ] **Step 1: Write `backend/scripts/backup-loop.sh`**

```sh
#!/bin/sh
# §10's schedule (I9). A loop, not host cron: the deployment has to be one
# directory and one `docker compose up`, and a crontab on the host is a
# second thing to install and a second thing to forget.
#
# Two cadences. Backups are cheap and frequent; a drill restores a whole
# database and is worth an interval of its own.
set -eu

BACKUP_DIR="${BUDGE_BACKUP_DIR:-/backups}"
BACKUP_INTERVAL="${BUDGE_BACKUP_INTERVAL:-3600}"
DRILL_INTERVAL="${BUDGE_DRILL_INTERVAL:-86400}"

since_drill=0

echo "budge backup loop: every ${BACKUP_INTERVAL}s, drill every ${DRILL_INTERVAL}s, into ${BACKUP_DIR}"

while true; do
	# `|| true` on the backup, deliberately: one failed run must not stop
	# the loop, or a transient database blip would end all backups until
	# somebody noticed the container had exited. The failure is on stdout
	# either way, and the drill is what turns a run of bad backups into a
	# loud signal.
	if podvinsya backup --to "${BACKUP_DIR}"; then
		echo "backup ok"
	else
		echo "BACKUP FAILED (exit $?)" >&2
	fi

	since_drill=$((since_drill + BACKUP_INTERVAL))
	if [ "${since_drill}" -ge "${DRILL_INTERVAL}" ]; then
		since_drill=0
		if podvinsya restore-drill --from "${BACKUP_DIR}"; then
			echo "restore drill ok"
		else
			# The one message an operator must never learn to ignore.
			echo "RESTORE DRILL FAILED — the backups are not restorable" >&2
		fi
	fi

	sleep "${BACKUP_INTERVAL}"
done
```

- [ ] **Step 2: Copy the script into the image**

In `backend/Dockerfile`, after the `COPY src/` line:

```dockerfile
COPY scripts/ ./scripts/
```

and remove `scripts/` from `backend/.dockerignore` if it matched.

- [ ] **Step 3: Uncomment the `backup` service in `compose.yaml`**

It was left commented in Task 2 Step 3 because the script did not exist yet.

- [ ] **Step 4: Prove the loop actually backs up and drills**

Run it with intervals short enough to observe, so the schedule is tested rather than assumed.

```bash
docker compose build; echo "exit=$?"
BUDGE_BACKUP_INTERVAL=5 BUDGE_DRILL_INTERVAL=5 docker compose up -d; echo "exit=$?"
```

Wait, then:

```bash
docker compose logs backup | tail -20
docker compose exec backup ls -R /backups | head -30
docker compose exec backup sh -c 'cat /backups/drills/*.json | tail -20'
```

Expected: the log shows `backup ok` and `restore drill ok`; `/backups/dumps` holds at least one `.dump` and its `.json`; a drill report exists with `"passed": true`. If the drill reports failures on an empty database, read them — an empty database has no matches to fold and must still pass.

- [ ] **Step 5: Prove a failing drill is loud**

A drill that cannot fail is not a drill. Break the newest backup and confirm the loop says so.

```bash
docker compose exec backup sh -c 'for f in /backups/dumps/*.dump; do echo broken > "$f"; done'
docker compose exec backup podvinsya restore-drill --from /backups; echo "exit=$?"
```

Expected: exit **1**, and the output reports `"passed": false`. Then confirm the scratch database was still dropped:

```bash
docker compose exec postgres psql -U "${POSTGRES_USER:-budge}" -lqt | grep drill || echo "ok: no scratch database left behind"
```

Expected: `ok: no scratch database left behind`.

- [ ] **Step 6: Tear down**

```bash
docker compose down -v
```

- [ ] **Step 7: Write `docs/operations.md`**

```markdown
# Running budge

§1.1's deployment profile: one machine on an isolated LAN, plain HTTP, one
operator. Everything below assumes that.

## First run

```bash
cp .env.example .env
# Fill in PODVINSYA_SECRET_KEY with a long random string:
#   openssl rand -hex 32
# Fill in PODVINSYA_HOST_PASSWORD with the hash of the operator's password:
#   echo -n 'the password' | docker compose run --rm --no-deps api podvinsya hash-password
docker compose up -d
```

The console is at `http://<this machine>:8080/host`. The stage screen's
link is shown on the match's setup screen — open it on the projector.

`migrate` runs to completion before `api` starts (§10); if `api` will not
come up, read `docker compose logs migrate` first.

## Health

```bash
curl -sS http://127.0.0.1:8080/health
```

`{"status":"ok","checks":{"database":true,"storage":true}}`. A `503` names
which of the two is down.

## Backups

The `backup` service takes one backup an hour and runs one restore drill a
day, into the `backups` volume:

```
dumps/20260823T200000Z.dump    the archive
dumps/20260823T200000Z.json    the digests it references, and the schema revision
media/aa/aaaa…                 the shared blob mirror, never pruned
drills/20260823T210000Z.json   what the last drill found
```

Check the drills, not the dumps — an archive nobody has restored is a file,
not a backup:

```bash
docker compose exec backup sh -c 'cat "$(ls -1 /backups/drills/*.json | tail -1)"'
```

`"passed": true` means: the archive restored, the schema is at a revision,
every match's event log still folds through the current domain code, and
every picture the database references was mirrored. A `false` names which
of those failed.

Take one by hand:

```bash
docker compose exec backup podvinsya backup --to /backups
docker compose exec backup podvinsya restore-drill --from /backups
```

## Restoring for real

The drill deliberately cannot do this — it only ever writes to a scratch
database (I1). Restoring over the live one is a decision a person makes:

```bash
docker compose stop api backup
docker compose exec postgres dropdb -U "$POSTGRES_USER" "$POSTGRES_DB"
docker compose exec postgres createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
docker compose cp backups:/backups/dumps/<stamp>.dump ./restore.dump
docker compose cp ./restore.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore --no-owner --no-privileges \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/restore.dump
docker compose start api backup
```

The pictures live in the object store, not in the dump. If MinIO's volume
is also gone, copy the mirror back into the bucket before starting `api` —
`/backups/media/<first two characters>/<digest>`, uploaded under the same
digest as its key.

## Changing the operator's password

```bash
echo -n 'the new password' | docker compose run --rm --no-deps api podvinsya hash-password
```

Put the output in `.env` and `docker compose up -d api`. Existing sessions
survive: they are signed with `PODVINSYA_SECRET_KEY`, which has not
changed. Changing *that* invalidates every session and every stage link.
```

- [ ] **Step 8: Commit**

```bash
git add backend/scripts backend/Dockerfile backend/.dockerignore compose.yaml docs/operations.md
git commit -m "feat(ops): back up on a schedule, and rehearse the restore on a slower one"
```

---

## Task 6: Review pass

No new behaviour. The technique is the one the previous two plans used: apply the change each test claims to kill, and confirm it fires.

- [ ] **Step 1: Mutation pass**

For each row: apply the mutation, run `cd backend && pytest tests/backup -q; echo "exit=$?"`, record which tests failed, then `git checkout -- <file>` and confirm green before the next.

| Mutation | Must kill |
| --- | --- |
| `paths.blob` — drop the digest check, join directly | every `test_it_refuses_anything_that_is_not_a_digest` case |
| `paths.stamps` — stop requiring a manifest beside the dump | "newest ignores a dump with no manifest" |
| `paths.stamp` — use local time instead of UTC | "it stamps in UTC whatever zone it is handed" |
| `dump._pg_dump` — `--format=plain` | "it writes an archive pg_restore recognises" |
| `dump.take` — write the manifest before running pg_dump | nothing yet — see Step 2 |
| `dump.take` — skip the blob copy loop entirely | "it copies the bytes behind every digest" |
| `dump.take` — re-fetch every blob, ignoring `destination.exists()` | "it does not refetch a blob it already mirrored" |
| `dump.take` — raise when `media.get` returns `None` | "a blob the store has lost does not stop the backup" |
| `drill.run` — restore into `database_url` instead of the scratch URL | **"it never touches the live database"** |
| `scratch.scratch_database` — drop the database only on success | "it drops the scratch database even when the drill fails" |
| `drill.run` — skip `_fold_every_match` | "it fails when a restored log no longer folds" |
| `drill.run` — skip the `missing_media` check | "it fails when a referenced picture was never mirrored" |
| `drill.run` — raise instead of reporting on an empty root | "it reports nothing to drill on an empty root" |
| `DrillReport.passed` — ignore `missing_media` | "it fails when a referenced picture was never mirrored" |

- [ ] **Step 2: Close every survivor**

At least one is expected: **writing the manifest before the dump** is the ordering `dump.py`'s docstring argues for, and nothing tests it. A run that crashed between the two would leave a manifest naming digests for an archive that does not exist — and `stamps()` would then report a backup whose `.dump` is absent. Write the test:

```python
async def test_a_manifest_without_its_archive_is_not_a_backup(
    clean_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest is written last, so a run interrupted mid-way leaves
    nothing `stamps()` will offer to the drill.

    Kills on: writing the manifest first — the drill would pick a backup
    whose archive was never finished and report a failure that looks like
    corruption rather than an interrupted run.
    """
    from podvinsya.backup import dump

    def explode(*_: object) -> None:
        raise dump.BackupFailed("interrupted")

    monkeypatch.setattr(dump, "_pg_dump", explode)
    root = BackupRoot(tmp_path)
    with pytest.raises(dump.BackupFailed):
        await take(root, database_url=DATABASE_URL, media=InMemoryMediaStore())
    assert root.stamps() == ()
```

Any other survivor is a missing test on the same terms: write it, confirm it fails under the mutation and passes without it, and report it prominently. Do not rationalise a survivor as "not worth testing" — this is the plan whose whole subject is that unverified things fail when they are needed.

- [ ] **Step 3: Verify the stack still comes up from a clean checkout**

The most likely thing to have rotted is the compose file, and nothing in the test suite touches it.

```bash
docker compose down -v 2>/dev/null
docker compose build; echo "build=$?"
docker compose up -d; echo "up=$?"
sleep 20
docker compose ps
curl -sS http://127.0.0.1:8080/health; echo
docker compose down -v
```

Expected: `migrate` exited 0, `api` healthy, `/health` reporting `ok` for both checks.

- [ ] **Step 4: Confirm clean and green**

```bash
cd backend && pytest -q; echo "pytest=$?"
mypy --strict src tests; echo "mypy=$?"
ruff check .; echo "ruff=$?"
cd .. && git status --porcelain
```

Expected: all 0, and `git status` reporting nothing but intended additions — every mutation reverted.

- [ ] **Step 5: Commit**

```bash
git add backend && git commit -m "test(backup): close the gaps the mutation pass found"
```

If the pass found nothing beyond Step 2's test, commit that alone and say so.

---

## Deliberately not built

- **Off-site backup shipping.** §10 asks for backups on a schedule with restore drills; it does not ask for replication to another machine, and §1.1's deployment is one box on an isolated LAN with no outbound route. The `backups` volume is a Docker volume an operator can copy off with `docker compose cp`; `docs/operations.md` says how. Adding an rclone target would mean inventing a credential story the spec has no place for.
- **Backup pruning.** Nothing deletes an old dump. The media mirror must never be pruned (I6), and the dumps are small next to it — an hourly `pg_dump` of a library-sized database is megabytes. A retention policy is a decision about how much history is worth keeping, which is the operator's, not this plan's.
- **Metrics or alerting.** The drill's verdict goes to stdout and to `drills/*.json`. §10 asks for a healthcheck, which already exists. Wiring Prometheus into a single-machine LAN deployment adds a service to watch the service.

## Done when

- `docker compose up -d` brings up postgres, minio, migrate, api, web and backup; `migrate` exits 0 before `api` starts; `/health` reports `ok` for both checks.
- Caddy serves the SPA on plain HTTP, proxies `/api` and `/ws` to the API, and falls through to `index.html` so `/host` and `/stage/:token` survive a reload.
- `podvinsya backup --to DIR` writes a custom-format archive, a manifest, and the blobs that manifest names.
- `podvinsya restore-drill --from DIR` restores the newest backup into a scratch database, folds every match's log through the domain, cross-checks the media, writes a report, drops the scratch database, and exits non-zero when any of that fails.
- A drill can never write to the configured database, and a test holds that.
- `cd backend && pytest`, `mypy --strict src tests` and `ruff check .` are all green.
- `.env` is not committed; `.env.example` is, and holds no real values.
- The branch `feature/infra` is left local — unpushed and unmerged.
