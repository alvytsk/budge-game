"""The command line. `migrate` exists because §10 requires migrations to be
applied as a separate step before the application starts, never at import."""

import argparse
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

import budge.db
from budge.config import Settings

# Located through the package, exactly as `_config` already locates the
# migrations directory below. The previous form walked three parents up
# from this file, which is `backend/` in a source tree and the
# interpreter's lib directory in a flat install — so `migrate` worked from
# a checkout and died in an installed container.
ALEMBIC_INI = Path(budge.__file__).resolve().parent / "alembic.ini"


def _board_size(value: str) -> tuple[int, int]:
    """`type=` for `--board`: parses `WIDTHxHEIGHT`, e.g. `4x3`.

    Validating here — an `ArgumentTypeError`, which argparse turns into its
    own usage-and-exit — means `--board 12`, `--board 4X3` and `--board 4x`
    are rejected with a message an operator can act on, instead of reaching
    `int("")` / `int("X3")` unguarded and surfacing as a raw `ValueError`
    traceback. Putting the rule on the argument itself, rather than in the
    branch that consumes it, is also where a second board-shaped flag could
    reuse it.
    """
    width, separator, height = value.partition("x")
    if not separator or not width.isdigit() or not height.isdigit():
        raise argparse.ArgumentTypeError(
            f"--board должен быть в формате ШИРИНАxВЫСОТА, например 4x3 "
            f"(получено {value!r})"
        )
    return int(width), int(height)


def _config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    # `script_location` in alembic.ini is a relative path, and Alembic
    # resolves it against the *invocation* directory rather than against
    # the ini file's own location — an operator running `budge migrate`
    # from anywhere but `backend/` would otherwise hit
    # `CommandError: Path doesn't exist`. Anchoring it to the installed
    # package makes the command work from any working directory.
    migrations_dir = Path(budge.db.__file__).parent / "migrations"
    config.set_main_option("script_location", str(migrations_dir))
    # `prepend_sys_path` has the identical relative-resolution behaviour
    # (Alembic inserts it into `sys.path` verbatim, not resolved against the
    # ini file). It happens to be harmless today because `env.py` imports
    # `budge` through its installed location, which is already on
    # `sys.path` regardless of the current working directory — but nothing
    # guarantees that stays true, so it is anchored here too.
    config.set_main_option("prepend_sys_path", str(migrations_dir.parent.parent.parent))
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="budge")
    subcommands = parser.add_subparsers(dest="command", required=True)
    migrate = subcommands.add_parser("migrate", help="apply migrations up to a revision")
    migrate.add_argument("--revision", default="head")
    subcommands.add_parser(
        "hash-password",
        help="read a password on stdin and print the value for BUDGE_HOST_PASSWORD",
    )
    serve = subcommands.add_parser("serve", help="run the API (migrate first — see §10)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    export_types = subcommands.add_parser(
        "export-types", help="generate the TypeScript contract from the Pydantic models"
    )
    export_types.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit non-zero and print a diff if the file is out of date",
    )
    backup = subcommands.add_parser("backup", help="take a backup (§10)")
    backup.add_argument("--to", default="/backups", help="the backup root directory")
    drill_parser = subcommands.add_parser(
        "restore-drill",
        help="restore the newest backup into a scratch database and prove it (§10)",
    )
    drill_parser.add_argument("--from", dest="source", default="/backups")
    seed = subcommands.add_parser(
        "seed-demo", help="fill the library and assemble a playable match (§G)"
    )
    seed.add_argument("--api", default="http://127.0.0.1:8000")
    seed.add_argument("--board", default="4x3", type=_board_size, help="width x height, e.g. 4x3")
    seed.add_argument("--players", type=int, default=3)
    seed.add_argument("--images", type=int, default=3)
    seed.add_argument(
        "--start", action="store_true", help="press «Начать» too, not just deal"
    )

    args = parser.parse_args(argv)
    if args.command == "migrate":
        command.upgrade(_config(Settings().database_url), args.revision)
        return 0
    if args.command == "hash-password":
        from budge.api.security import hash_password

        # stdin, not argv: a password on a command line lands in shell
        # history and in `ps` output for every user on the machine.
        print(hash_password(sys.stdin.readline().rstrip("\n")))
        return 0
    if args.command == "serve":
        # Imported here rather than at module scope: `budge migrate`
        # must not pull in FastAPI, uvicorn and the whole API graph to run
        # one Alembic command — and `ApiSettings()` is constructed inside
        # this branch for the same reason `Settings()` is constructed
        # inside `migrate`'s, so neither command demands the other's
        # environment (§10, and the plan's ruling 11).
        import uvicorn

        from budge.api.app import build_app
        from budge.api.settings import ApiSettings

        uvicorn.run(build_app(ApiSettings()), host=args.host, port=args.port)
        return 0
    if args.command == "export-types":
        # Imported here for the same reason `serve`'s imports are: the
        # migrate step must not pull in the whole API model tree to run one
        # Alembic command.
        from budge.contracts import export

        if not args.check:
            print(export.write())
            return 0
        difference = export.check()
        if difference is None:
            return 0
        # Flushed before the message goes to stderr: the two streams are
        # interleaved in a CI log, and a diff printed after its own summary
        # reads as though it belonged to the next step.
        print(difference, end="", flush=True)
        print(
            f"\n{export.CONTRACTS_PATH} is out of date. "
            "Run `budge export-types` and commit the result.",
            file=sys.stderr,
        )
        return 1
    if args.command == "backup":
        # Imported here for the reason `serve`'s imports are: `migrate`
        # must not pull the media stack in to run one Alembic command.
        import asyncio

        from budge.api.settings import ApiSettings
        from budge.backup.dump import take
        from budge.backup.paths import BackupRoot
        from budge.media.s3 import S3MediaStore

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
    if args.command == "restore-drill":
        # Imported here for the reason `serve`'s imports are; and this
        # branch constructs `Settings`, not `ApiSettings`: the drill needs a
        # database and nothing else, and demanding a signing key to
        # rehearse a restore would be the mistake `migrate` already avoids.
        import asyncio

        from budge.backup import drill
        from budge.backup.paths import BackupRoot

        report = asyncio.run(
            drill.run(BackupRoot(Path(args.source)), database_url=Settings().database_url)
        )
        print(json.dumps({"passed": report.passed, "dump": report.dump}, ensure_ascii=False))
        # Non-zero on failure, so the scheduler and any human running this
        # by hand both learn the answer without reading a log.
        return 0 if report.passed else 1
    if args.command == "seed-demo":
        # Imported here for the reason `serve`'s imports are: `migrate` must
        # not pull the API model tree in to run one Alembic command.
        import asyncio

        from budge.api.security import mint_session
        from budge.api.settings import ApiSettings
        from budge.demo.http import UrllibCaller
        from budge.demo.seed import DemoFailed, DemoPlan, run
        from budge.runtime.clock import SystemClock

        settings = ApiSettings()
        # §G.3: `BUDGE_HOST_PASSWORD` is a scrypt hash and cannot be logged
        # in with. The signing key can mint the same cookie the login route
        # mints, and holding it is already the operator's authority — it is
        # what signs stage links (§7.5).
        caller = UrllibCaller(
            args.api, mint_session(settings.secret_key, issued_at=SystemClock().now())
        )
        try:
            demo_report = asyncio.run(
                run(
                    caller,
                    DemoPlan(
                        board=args.board,
                        players=args.players,
                        images=args.images,
                        start=args.start,
                    ),
                )
            )
        except DemoFailed as failure:
            # An operator running this needs to know the demo stopped and
            # why, not an uncaught-exception traceback from inside asyncio.
            print(f"демо остановлено: {failure}", file=sys.stderr)
            return 1
        print(f"партия: {args.api}/host/match/{demo_report.match_id}")
        print(f"экран сцены: {args.api}/stage/{demo_report.stage_token}")
        print(
            f"создано тем: {demo_report.categories_created}, "
            f"картинок: {demo_report.images_created}"
        )
        return 0
    return 1  # pragma: no cover - argparse rejects anything else first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
