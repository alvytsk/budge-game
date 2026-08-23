"""The command line, exercised through `main` rather than a subprocess.

`migrate` already has its own integration coverage in `tests/db`; what is
tested here is the one subcommand that has no database in it at all.
"""

import io

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

    monkeypatch.setenv("PODVINSYA_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("PODVINSYA_SECRET_KEY", "a-key")
    monkeypatch.setenv("PODVINSYA_HOST_PASSWORD", "a-hash")
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

    monkeypatch.setenv("PODVINSYA_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("PODVINSYA_SECRET_KEY", "a-key")
    monkeypatch.setenv("PODVINSYA_HOST_PASSWORD", "a-hash")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("serve must not run migrations")

    monkeypatch.setattr(alembic_command, "upgrade", refuse)

    assert main(["serve"]) == 0
