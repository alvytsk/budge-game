"""The command line. `migrate` exists because §10 requires migrations to be
applied as a separate step before the application starts, never at import."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

import podvinsya.db
from podvinsya.config import Settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    # `script_location` in alembic.ini is a relative path, and Alembic
    # resolves it against the *invocation* directory rather than against
    # the ini file's own location — an operator running `podvinsya migrate`
    # from anywhere but `backend/` would otherwise hit
    # `CommandError: Path doesn't exist`. Anchoring it to the installed
    # package makes the command work from any working directory.
    migrations_dir = Path(podvinsya.db.__file__).parent / "migrations"
    config.set_main_option("script_location", str(migrations_dir))
    # `prepend_sys_path` has the identical relative-resolution behaviour
    # (Alembic inserts it into `sys.path` verbatim, not resolved against the
    # ini file). It happens to be harmless today because `env.py` imports
    # `podvinsya` through its installed location, which is already on
    # `sys.path` regardless of the current working directory — but nothing
    # guarantees that stays true, so it is anchored here too.
    config.set_main_option("prepend_sys_path", str(migrations_dir.parent.parent.parent))
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="podvinsya")
    subcommands = parser.add_subparsers(dest="command", required=True)
    migrate = subcommands.add_parser("migrate", help="apply migrations up to a revision")
    migrate.add_argument("--revision", default="head")
    subcommands.add_parser(
        "hash-password",
        help="read a password on stdin and print the value for PODVINSYA_HOST_PASSWORD",
    )
    serve = subcommands.add_parser("serve", help="run the API (migrate first — see §10)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command == "migrate":
        command.upgrade(_config(Settings().database_url), args.revision)
        return 0
    if args.command == "hash-password":
        from podvinsya.api.security import hash_password

        # stdin, not argv: a password on a command line lands in shell
        # history and in `ps` output for every user on the machine.
        print(hash_password(sys.stdin.readline().rstrip("\n")))
        return 0
    if args.command == "serve":
        # Imported here rather than at module scope: `podvinsya migrate`
        # must not pull in FastAPI, uvicorn and the whole API graph to run
        # one Alembic command — and `ApiSettings()` is constructed inside
        # this branch for the same reason `Settings()` is constructed
        # inside `migrate`'s, so neither command demands the other's
        # environment (§10, and the plan's ruling 11).
        import uvicorn

        from podvinsya.api.app import build_app
        from podvinsya.api.settings import ApiSettings

        uvicorn.run(build_app(ApiSettings()), host=args.host, port=args.port)
        return 0
    return 1  # pragma: no cover - argparse rejects anything else first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
