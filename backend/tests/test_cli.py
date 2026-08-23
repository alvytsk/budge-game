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
