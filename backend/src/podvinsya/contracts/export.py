"""Where the generated TypeScript goes, and how divergence is reported.

§7.6 wants divergence caught in CI, which means the artifact is committed
and the check is a comparison against it. A file generated at build time
and never committed has nothing to diverge *from* — the check would
regenerate it and compare it with itself.
"""

import difflib
from pathlib import Path

from podvinsya.contracts.schema import contract_schema
from podvinsya.contracts.typescript import emit

# `backend/src/podvinsya/contracts/export.py` → the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[4]

# §9.3: «Обе поверхности живут в одном приложении и делят сгенерированные
# типы», and FSD puts what both surfaces share at the `shared/` layer.
CONTRACTS_PATH = _REPO_ROOT / "frontend" / "src" / "shared" / "api" / "contracts.ts"


def render() -> str:
    return emit(contract_schema())


def write(path: Path | None = None) -> Path:
    target = path or CONTRACTS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    return target


def check(path: Path | None = None) -> str | None:
    """The unified diff between the models and the committed file, or
    `None` when they agree.

    A diff rather than a boolean: the CI failure this produces is read by
    somebody who has just changed a model, and «they differ» would send
    them to run the generator locally to find out how.
    """
    target = path or CONTRACTS_PATH
    expected = render()
    actual = target.read_text(encoding="utf-8") if target.exists() else ""
    if actual == expected:
        return None
    return "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{target} (committed)",
            tofile="generated from the Pydantic models",
        )
    )
