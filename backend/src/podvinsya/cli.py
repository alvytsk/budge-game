"""The command line. `migrate` exists because §10 requires migrations to be
applied as a separate step before the application starts, never at import."""

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from podvinsya.config import Settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
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
