"""JSON Schema to TypeScript, for exactly the constructs this contract uses.

Ruling 2 is the whole design: every node is matched against a construct
this emitter knows, and anything left over raises `UnsupportedSchema`
naming the path to it. There is no `any` fallback and no `unknown`
fallback. A generator that degrades quietly emits TypeScript that compiles
and lies, and the CI check §11 asks for would go on passing over it —
which would make the check worse than useless, because it would be
believed.

Everything is emitted alphabetically. The artifact is committed and
compared, so a stable order is a correctness property: `$defs` arrives in
model *discovery* order, which changes when an import moves.
"""

from typing import Any

HEADER = """// Generated from the Pydantic models by `podvinsya export-types`.
// Do not edit: run `podvinsya export-types` and commit the result.
// The CI job `contracts` fails if this file and the models disagree (§7.6).
"""

_PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}

# Annotation, not structure: none of these changes what a value may be, so
# an emitter that ignores them is not guessing.
#
# `propertyNames` is here because the only thing it constrains in this
# contract is the *key* type of a `Record`, and TypeScript index signatures
# are `string` regardless — a UUID key and a plain string key are the same
# type on that side of the wire.
_IGNORED_KEYS = frozenset({"title", "description", "default", "examples", "propertyNames"})


class UnsupportedSchema(Exception):
    """A schema node this emitter will not guess at.

    Raised rather than degraded to `any`, so a construct nobody has taught
    this module about stops the export instead of producing a contract that
    is quietly wrong.
    """

    def __init__(self, path: str, node: object) -> None:
        super().__init__(f"{path}: this emitter does not understand {node!r}")
        self.path = path


def _literal(value: object, path: str) -> str:
    if isinstance(value, bool):
        # Before the `int` check: `bool` is a subclass of `int`, and
        # `True` would otherwise be emitted as `1`.
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    raise UnsupportedSchema(path, value)


def _type_of(node: dict[str, Any], path: str) -> str:
    """One schema node to one TypeScript type expression."""
    if "$ref" in node:
        return str(node["$ref"]).rsplit("/", 1)[-1]
    if "const" in node:
        return _literal(node["const"], path)
    if "enum" in node:
        return " | ".join(_literal(value, path) for value in node["enum"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            return " | ".join(
                _type_of(branch, f"{path}[{index}]") for index, branch in enumerate(node[key])
            )

    kind = node.get("type")
    if kind == "array":
        items = node.get("items")
        if not isinstance(items, dict) or "prefixItems" in node:
            # An array with no `items` is a list of anything, which is the
            # one shape this contract must never contain: it would be the
            # `any` this emitter refuses, arriving through the back door.
            # A tuple (`prefixItems`) is refusable rather than wrong — no
            # model produces one, and guessing at one would be guessing.
            raise UnsupportedSchema(path, node)
        inner = _type_of(items, f"{path}[]")
        # `A | B[]` parses as `A | (B[])`. A union that is not parenthesised
        # would silently mean something else.
        return f"({inner})[]" if " " in inner else f"{inner}[]"
    if kind == "object":
        values = node.get("additionalProperties")
        if isinstance(values, dict):
            return f"Record<string, {_type_of(values, path + '{}')}>"
        # An object with neither `properties` nor a typed
        # `additionalProperties` is an open bag, which is `any` by another
        # name. `properties` is handled by `_interface`, never here.
        raise UnsupportedSchema(path, node)
    if isinstance(kind, str) and kind in _PRIMITIVES:
        # A sibling key this emitter does not know about is a constraint it
        # would be dropping — a `pattern` or a `minimum` silently discarded
        # tells the front end that any value of the type will do.
        unknown = set(node) - _IGNORED_KEYS - {"type", "format"}
        if unknown:
            raise UnsupportedSchema(path, node)
        return _PRIMITIVES[kind]
    raise UnsupportedSchema(path, node)


def _interface(name: str, node: dict[str, Any]) -> str:
    required = set(node.get("required", ()))
    lines = [f"export interface {name} {{"]
    for field, schema in node.get("properties", {}).items():
        # Ruling 6: a `const` is always sent and is what a consumer narrows
        # on, so it is required whatever `required` says.
        optional = field not in required and "const" not in schema
        mark = "?" if optional else ""
        lines.append(f"  {field}{mark}: {_type_of(schema, f'{name}.{field}')};")
    lines.append("}")
    return "\n".join(lines)


def _alias(name: str, node: dict[str, Any]) -> str:
    return f"export type {name} = {_type_of(node, name)};"


def emit(document: dict[str, Any]) -> str:
    """The whole document as one TypeScript module."""
    definitions = document["$defs"]
    blocks = [
        _interface(name, definitions[name])
        if definitions[name].get("type") == "object" and "properties" in definitions[name]
        else _alias(name, definitions[name])
        for name in sorted(definitions)
    ]
    return HEADER + "\n" + "\n\n".join(blocks) + "\n"
