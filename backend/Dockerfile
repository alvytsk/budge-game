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
