# The API, and the backup tooling that shares its code (I5).
FROM python:3.12-slim

# `postgresql-client` is here because of I5: the restore drill has to run
# `pg_restore` AND import `podvinsya.domain` to fold the restored log, so
# the tools and the domain must live in one image. `--no-install-recommends`
# keeps that to the client binaries rather than a server.
#
# Major 16 from PGDG rather than whatever Debian ships. The base image is
# trixie, whose `postgresql-client` is 17, and a 17 archive restored into
# this stack's `postgres:16-alpine` fails on `SET transaction_timeout = 0`
# — a parameter 16 has never heard of. That made every drill fail on the
# dump it had itself just taken. A dump is only restorable into a server
# of its own major or newer, so the client major has to be the server's:
# 16 here, in `compose.test.yaml` and in CI alike.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && install -d /etc/apt/keyrings \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /etc/apt/keyrings/pgdg.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source: the layer cache then survives every edit
# that does not touch pyproject.toml.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY alembic.ini ./
# Editable, and that is load-bearing rather than a convenience. `cli.py`
# locates `alembic.ini` three directories above itself — `backend/` in the
# source tree. Installed flat into site-packages the same arithmetic lands
# on `/usr/local/lib/python3.12/alembic.ini`, which does not exist, and
# `podvinsya migrate` dies in `fileConfig` before it opens a connection.
# An editable install keeps the runtime layout the same shape as the source
# layout, so `/app/alembic.ini` — the file the line above copies — is the
# file the CLI actually finds.
RUN pip install --no-cache-dir --no-deps -e .

# Unprivileged: nothing here writes to the image, and the backup volume is
# mounted with this uid's ownership by compose.
RUN useradd --create-home --uid 10001 budge
USER budge

# No CMD that migrates. §10 and I8 make migration a separate step, and an
# image that migrated on start would make that impossible to honour.
CMD ["podvinsya", "serve", "--host", "0.0.0.0", "--port", "8000"]
