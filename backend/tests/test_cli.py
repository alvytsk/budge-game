"""The command line, exercised through `main` rather than a subprocess.

`migrate` already has its own integration coverage in `tests/db`; what is
tested here is the one subcommand that has no database in it at all.
"""

import io
from pathlib import Path

import pytest

from podvinsya.api.security import verify_password
from podvinsya.cli import main


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
        "PODVINSYA_DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:1/x",
        "PODVINSYA_SECRET_KEY": "a-key",
        "PODVINSYA_HOST_PASSWORD": "a-hash",
        "PODVINSYA_S3_ENDPOINT": "http://127.0.0.1:1",
        "PODVINSYA_S3_ACCESS_KEY": "an-access-key",
        "PODVINSYA_S3_SECRET_KEY": "a-secret-key",
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
    monkeypatch.delenv("PODVINSYA_S3_ENDPOINT")
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
    from podvinsya.contracts import export

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
    from podvinsya.contracts import export

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
    from podvinsya.contracts import export

    target = tmp_path / "shared" / "api" / "contracts.ts"
    monkeypatch.setattr(export, "CONTRACTS_PATH", target)

    assert main(["export-types"]) == 0

    assert str(target) in capsys.readouterr().out
    assert "export interface StageFrame" in target.read_text(encoding="utf-8")
