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

    args = parser.parse_args(argv)
    if args.command == "migrate":
        command.upgrade(_config(Settings().database_url), args.revision)
        return 0
    return 1  # pragma: no cover - argparse rejects anything else first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
