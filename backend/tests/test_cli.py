"""The command line, exercised through `main` rather than a subprocess.

`migrate` already has its own integration coverage in `tests/db`; what is
tested here is the one subcommand that has no database in it at all.
"""

import io
from pathlib import Path

import pytest

from budge.api.security import verify_password
from budge.cli import main


def test_hash_password_prints_a_hash_that_verifies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: printing the password, or hashing a line that still has
    its trailing newline — either would make the printed value fail to
    verify against what the operator typed."""
    monkeypatch.setattr("sys.stdin", io.StringIO("hunter2\n"))
    assert main(["hash-password"]) == 0
    assert verify_password("hunter2", capsys.readouterr().out.strip())


def test_hash_password_does_not_print_the_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: `print(password)` instead of `print(hash_password(...))`,
    which the test above would still pass if `verify_password` were ever
    weakened — this one fails on the leak directly."""
    monkeypatch.setattr("sys.stdin", io.StringIO("hunter2\n"))
    main(["hash-password"])
    assert "hunter2" not in capsys.readouterr().out


def test_a_password_containing_spaces_survives_the_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: `strip()` instead of `rstrip("\\n")`, which would silently
    hash something other than the passphrase the operator typed."""
    passphrase = "  correct horse battery staple  "
    monkeypatch.setattr("sys.stdin", io.StringIO(passphrase + "\n"))
    main(["hash-password"])
    assert verify_password(passphrase, capsys.readouterr().out.strip())


def serve_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything `ApiSettings()` requires, and nothing it defaults.

    Set in one place because the list is the point: `serve` constructs its
    settings from the environment, and every field without a default is one
    a deployment must be told about explicitly — a database, a signing key,
    a password hash, and the object store §10 asks for.
    """
    for name, value in {
        "BUDGE_DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:1/x",
        "BUDGE_SECRET_KEY": "a-key",
        "BUDGE_HOST_PASSWORD": "a-hash",
        "BUDGE_S3_ENDPOINT": "http://127.0.0.1:1",
        "BUDGE_S3_ACCESS_KEY": "an-access-key",
        "BUDGE_S3_SECRET_KEY": "a-secret-key",
    }.items():
        monkeypatch.setenv(name, value)


def test_serve_needs_every_setting_that_has_no_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kills on: giving `s3_endpoint` or either credential a default. A
    process that quietly pointed at somebody's scratch bucket would be
    worse than one that refused to start — the same reason
    `database_url` has none."""
    import uvicorn
    from pydantic import ValidationError

    serve_environment(monkeypatch)
    monkeypatch.delenv("BUDGE_S3_ENDPOINT")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)

    with pytest.raises(ValidationError):
        main(["serve"])


def test_serve_starts_uvicorn_with_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills on: passing an import string instead of the built app, which
    would make uvicorn construct a second `ApiSettings()` in a worker
    process — and `--host`/`--port` are asserted because their defaults are
    the difference between "listening locally" and "listening to the whole
    meeting-room network"."""
    import uvicorn

    calls: list[tuple[object, str, int]] = []

    def fake_run(app: object, *, host: str, port: int, **rest: object) -> None:
        calls.append((app, host, port))

    serve_environment(monkeypatch)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    assert main(["serve", "--port", "9001"]) == 0

    from fastapi import FastAPI

    (app, host, port), = calls
    assert isinstance(app, FastAPI)
    assert (host, port) == ("127.0.0.1", 9001)


def test_serve_does_not_apply_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    """§10: «Миграции применяются отдельным шагом до старта приложения, а
    не при импорте.»

    Kills on: calling `command.upgrade` in `serve` or in the lifespan — a
    convenience that turns every restart mid-show into a schema change
    nobody asked for."""
    import uvicorn
    from alembic import command as alembic_command

    serve_environment(monkeypatch)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("serve must not run migrations")

    monkeypatch.setattr(alembic_command, "upgrade", refuse)

    assert main(["serve"]) == 0


def test_export_types_check_passes_against_the_committed_file() -> None:
    """Kills on: `--check` writing the file, which would make the CI job
    pass by fixing the divergence instead of reporting it."""
    assert main(["export-types", "--check"]) == 0


def test_export_types_check_fails_against_a_stale_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§11: «расхождение Pydantic и TypeScript валит CI» — a non-zero exit
    is what "fails CI" means mechanically.

    Kills on: returning 0 on a difference, which leaves the CI job green
    over a contract that has drifted."""
    from budge.contracts import export

    stale = tmp_path / "contracts.ts"
    stale.write_text("nothing like the real thing\n", encoding="utf-8")
    monkeypatch.setattr(export, "CONTRACTS_PATH", stale)

    assert main(["export-types", "--check"]) == 1
    captured = capsys.readouterr()
    assert "out of date" in captured.err
    assert "StageFrame" in captured.out


def test_export_types_check_does_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: `--check` calling `write`. A check that repaired what it
    was checking would report a clean tree on every second run, and the
    divergence would reach the front end anyway."""
    from budge.contracts import export

    stale = tmp_path / "contracts.ts"
    stale.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(export, "CONTRACTS_PATH", stale)

    main(["export-types", "--check"])
    capsys.readouterr()

    assert stale.read_text(encoding="utf-8") == "stale\n"


def test_export_types_writes_and_prints_where(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: writing silently. The operator running this needs to know
    which file to commit, and the path is the whole of that answer."""
    from budge.contracts import export

    target = tmp_path / "shared" / "api" / "contracts.ts"
    monkeypatch.setattr(export, "CONTRACTS_PATH", target)

    assert main(["export-types"]) == 0

    assert str(target) in capsys.readouterr().out
    assert "export interface StageFrame" in target.read_text(encoding="utf-8")


def test_backup_needs_the_media_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kills on: constructing `Settings` rather than `ApiSettings` — the
    backup would start, dump the database, and silently mirror nothing,
    because it would have no store to read from."""
    from pydantic import ValidationError

    serve_environment(monkeypatch)
    for name in ("BUDGE_S3_ENDPOINT", "BUDGE_S3_ACCESS_KEY", "BUDGE_S3_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        main(["backup", "--to", "/tmp/does-not-matter"])


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
    import budge

    from budge.cli import ALEMBIC_INI

    assert ALEMBIC_INI.is_file()
    assert ALEMBIC_INI.parent == Path(budge.__file__).parent


def test_seed_demo_builds_the_plan_from_the_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: `--board` parsed wrong (e.g. width and height swapped, or
    left as the raw string), or the plan built from a default instead of
    what was actually typed on the command line."""
    from budge.demo import seed as seed_module

    serve_environment(monkeypatch)
    captured: dict[str, object] = {}

    async def fake_run(
        caller: object, plan: seed_module.DemoPlan
    ) -> seed_module.DemoReport:
        captured["plan"] = plan
        return seed_module.DemoReport(
            match_id="m1",
            stage_token="tok",
            categories_created=2,
            images_created=6,
            started=False,
        )

    monkeypatch.setattr(seed_module, "run", fake_run)

    assert main(["seed-demo", "--board", "4x3", "--players", "3"]) == 0

    plan = captured["plan"]
    assert isinstance(plan, seed_module.DemoPlan)
    assert plan == seed_module.DemoPlan(board=(4, 3), players=3, images=3, start=False)
    out = capsys.readouterr().out
    assert "m1" in out
    assert "tok" in out


def test_seed_demo_rejects_a_malformed_board(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--board 12`, `--board 4X3` and `--board 4x` reach `int("")` /
    `int("X3")` unvalidated and used to surface as an uncaught `ValueError`
    traceback instead of a message an operator can act on.

    Kills on: no validation on `--board`, or validation that lets a
    malformed value through to `DemoPlan`."""
    serve_environment(monkeypatch)

    for malformed in ("12", "4X3", "4x"):
        with pytest.raises(SystemExit) as excinfo:
            main(["seed-demo", "--board", malformed])
        assert excinfo.value.code == 2
        assert "--board" in capsys.readouterr().err


def test_seed_demo_reports_a_demo_failure_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kills on: letting `DemoFailed` escape `asyncio.run` uncaught — the
    operator would see a Python traceback instead of a diagnosable
    message, and the process would still exit non-zero by accident rather
    than by design."""
    from budge.demo import seed as seed_module

    serve_environment(monkeypatch)

    async def failing_run(
        caller: object, plan: seed_module.DemoPlan
    ) -> seed_module.DemoReport:
        raise seed_module.DemoFailed("POST /api/library/categories answered 500: {}")

    monkeypatch.setattr(seed_module, "run", failing_run)

    assert main(["seed-demo", "--board", "4x3"]) == 1
    err = capsys.readouterr().err
    assert "демо остановлено" in err
    assert "500" in err


def test_the_help_names_the_program_the_user_typed(capsys: pytest.CaptureFixture[str]) -> None:
    """`prog=` has no other coverage, so a missed rename here would ship a
    CLI whose --help and every error message name a program that does not
    exist — and nothing would ever say so.

    Kills on: leaving `prog="podvinsya"` behind.
    """
    with pytest.raises(SystemExit):
        main(["--help"])
    assert capsys.readouterr().out.startswith("usage: budge")
