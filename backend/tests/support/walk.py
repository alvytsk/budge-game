"""Two whole-tree walks the leak tests are built on.

§11 asks for a projection test «по всему дереву кадра, а не по известным
полям», and the schema tests ask the same question of the shape rather than
of a value. Both are the same traversal, and both are load-bearing enough
that having two copies of them — one per test module, drifting — would be
the place a leak eventually hides.
"""

from typing import Any


def every_string(node: object) -> list[str]:
    """Every string anywhere in a dumped model, at any depth.

    Keys as well as values: a frame that carried an answer as a dictionary
    *key* would pass a walk that only looked at values.
    """
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for key, value in node.items() for s in every_string(key) + every_string(value)]
    if isinstance(node, (list, tuple)):
        return [s for item in node for s in every_string(item)]
    return []


def property_names(schema: dict[str, Any]) -> set[str]:
    """Every property name reachable anywhere in a JSON Schema.

    The whole document is walked, `$defs` included, rather than following
    `$ref`s: an unreachable definition in a frame's own schema is still a
    field somebody wrote, and a test that quietly skipped it would be the
    one place a leak could hide.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                found.update(str(key) for key in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found
