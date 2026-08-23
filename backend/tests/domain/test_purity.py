import ast
import pathlib

import pytest

DOMAIN = pathlib.Path(__file__).resolve().parents[2] / "src" / "budge" / "domain"

# The path is spelled out rather than derived from an import, so a rename
# of the package would leave this pointing at a directory that no longer
# exists -- and `iterdir()` on nothing walks nothing and passes. Fail here
# instead, loudly, before the walk that is supposed to be the test.
assert DOMAIN.is_dir(), f"the domain package is not at {DOMAIN}"

FORBIDDEN_MODULES = {
    "asyncio", "random", "secrets", "time", "os", "socket", "pathlib",
    "sqlalchemy", "fastapi", "httpx", "requests",
}


def _module_files() -> list[pathlib.Path]:
    return sorted(DOMAIN.glob("*.py"))


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_domain_imports_no_io_or_randomness(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN_MODULES), (
        f"{path.name} imports {sorted(imported & FORBIDDEN_MODULES)}"
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_domain_never_reads_a_clock(path: pathlib.Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert "datetime.now" not in source, f"{path.name} reads a clock; inject ctx.now instead"
    assert "utcnow" not in source, f"{path.name} reads a clock; inject ctx.now instead"


def test_decide_is_deterministic_for_the_same_inputs() -> None:
    from budge.domain.actions import DeclareAttack
    from budge.domain.context import DecisionContext
    from budge.domain.decide import decide
    from budge.domain.rules import legal_targets

    from .conftest import IMAGE_POOL, BASE_TIME, build_running_state

    state, _ = build_running_state(4)
    attacker = state.current_player()
    attacking = next(
        g for g in state.groups.values() if g.owner == attacker and legal_targets(state, g.id)
    )
    defending = sorted(legal_targets(state, attacking.id))[0]
    command = DeclareAttack(attacking_group=attacking.id, defending_group=defending)
    ctx = DecisionContext(now=BASE_TIME, image_order=IMAGE_POOL)

    assert decide(state, command, ctx) == decide(state, command, ctx)
