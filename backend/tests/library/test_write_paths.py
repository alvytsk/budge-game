"""§5.3's «бамп обеспечивается ровно в одном месте, и это покрыто тестом».

Source analysis, not behaviour: the property is about the shape of the
codebase, so it reads the codebase. No database and no event loop, which is
why it lives apart from `test_catalogue.py` and its integration mark.
"""

import ast
from pathlib import Path

import podvinsya

# The one module allowed to write to the library. §5.3 makes this a
# structural requirement, not a convention: a second writer is exactly what
# "slips past the FOR SHARE lock".
THE_ONLY_WRITER = "library/catalogue.py"

LIBRARY_MODELS = {"Category", "Image"}
WRITING_CALLS = {"add", "add_all", "insert", "update", "delete"}


def _writes_a_library_model(node: ast.Call) -> bool:
    """True for `session.add(Category(...))`, `update(Image)`, `delete(...)`
    and friends — any call that both looks like a write and names a library
    model in its arguments."""
    function = node.func
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
    if name not in WRITING_CALLS:
        return False
    for argument in ast.walk(node):
        if isinstance(argument, ast.Name) and argument.id in LIBRARY_MODELS:
            return True
        if isinstance(argument, ast.Attribute) and argument.attr in LIBRARY_MODELS:
            return True
    return False


def test_no_write_outside_the_catalogue() -> None:
    """§5.3: «Каждая семантическая правка категории бампает
    `categories.version`… Бамп обеспечивается ровно в одном месте, и это
    покрыто тестом.» This is that test.

    It parses every module under `src/podvinsya/` and fails on any call
    that writes a library model outside `library/catalogue.py`. A second
    write path is precisely what §5.3 warns about: it would change what a
    category *is* without moving the row that selection locked.

    Kills on: an admin route updating a row directly because it is one line
    shorter than calling the catalogue — the version would not move, a
    concurrent selection holding `FOR SHARE` would not be protected by it,
    and the match would be dealt from a library that changed underneath it.
    """
    root = Path(podvinsya.__file__).parent
    offenders: list[str] = []
    for module in sorted(root.rglob("*.py")):
        relative = module.relative_to(root).as_posix()
        if relative == THE_ONLY_WRITER:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _writes_a_library_model(node):
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders


def test_the_writer_detector_finds_a_write() -> None:
    """A typo in `WRITING_CALLS` or `LIBRARY_MODELS` would make the test
    above pass against a codebase that wrote from everywhere.

    Kills on: either list going stale — this asserts the detector fires on
    the shapes it is meant to catch."""
    for source in (
        "session.add(Category(id=1))",
        "await session.execute(update(Category).values(title='x'))",
        "session.add(Image(id=1))",
        "await session.execute(delete(Image))",
    ):
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert any(_writes_a_library_model(call) for call in calls), source


