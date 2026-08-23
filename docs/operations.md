# Running budge

§1.1's deployment profile: one machine on an isolated LAN, plain HTTP, one
operator. Everything below assumes that.

## First run

```bash
cp .env.example .env
# Fill in BUDGE_SECRET_KEY with a long random string:
#   openssl rand -hex 32
# Fill in BUDGE_HOST_PASSWORD with the hash of the operator's password:
#   echo -n 'the password' | docker compose run --rm --no-deps api budge hash-password
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
docker compose exec backup budge backup --to /backups
docker compose exec backup budge restore-drill --from /backups
```

## Restoring for real

The drill deliberately cannot do this — it only ever writes to a scratch
database (I1). Restoring over the live one is a decision a person makes:

```bash
docker compose stop api backup
# `sh -c` on each: the variables live in the postgres container's
# environment, and an unquoted expansion here would be the host shell's
# empty string.
docker compose exec postgres sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose cp backup:/backups/dumps/<stamp>.dump ./restore.dump
docker compose cp ./restore.dump postgres:/tmp/restore.dump
docker compose exec postgres sh -c 'pg_restore --no-owner --no-privileges \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/restore.dump'
docker compose start api backup
```

The pictures live in the object store, not in the dump. If MinIO's volume
is also gone, copy the mirror back into the bucket before starting `api` —
`/backups/media/<first two characters>/<digest>`, uploaded under the same
digest as its key.

## Changing the operator's password

```bash
echo -n 'the new password' | docker compose run --rm --no-deps api budge hash-password
```

Put the output in `.env` and `docker compose up -d api`. Existing sessions
survive: they are signed with `BUDGE_SECRET_KEY`, which has not
changed. Changing *that* invalidates every session and every stage link.
