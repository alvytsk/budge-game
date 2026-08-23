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

HEADER = """// Generated from the Pydantic models by `budge export-types`.
// Do not edit: run `budge export-types` and commit the result.
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

# Constraints TypeScript cannot express as a type, and which are therefore
# carried into the output as a doc comment on the property.
#
# This is not the `any` fallback ruling 2 forbids. That fallback is
# dangerous because it *hides* what the emitter did not understand; these
# are understood, and the information reaches the reader in the only form
# the target language has for it. Anything outside this list still raises,
# so a constraint nobody has decided how to carry stops the export.
_DOCUMENTED_CONSTRAINTS = (
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
)


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
        unknown = (
            set(node) - _IGNORED_KEYS - set(_DOCUMENTED_CONSTRAINTS) - {"type", "format"}
        )
        if unknown:
            raise UnsupportedSchema(path, node)
        return _PRIMITIVES[kind]
    raise UnsupportedSchema(path, node)


# Past this, a property is wrapped one union member to a line. The number
# is not a style rule: this file is committed, and the CI check's failure is
# read as a diff. A union on one line changes that whole line when a single
# member is added — which, for the command union, is every later plan.
_LINE_BUDGET = 100


def _split_top_level_union(expression: str) -> list[str]:
    """Split on ` | ` at bracket depth zero.

    Naive splitting would cut inside `Record<string, A | B>` and
    `(A | B)[]`, producing members that are not types.
    """
    parts: list[str] = []
    depth = 0
    current = ""
    index = 0
    while index < len(expression):
        char = expression[index]
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        if depth == 0 and expression[index : index + 3] == " | ":
            parts.append(current)
            current = ""
            index += 3
            continue
        current += char
        index += 1
    parts.append(current)
    return parts


def _property(field: str, mark: str, expression: str) -> str:
    one_line = f"  {field}{mark}: {expression};"
    if len(one_line) <= _LINE_BUDGET:
        return one_line
    members = _split_top_level_union(expression)
    if len(members) == 1:
        # Long, but not a union — nothing to wrap on, and breaking it
        # anywhere else would be a guess about where it reads best.
        return one_line
    wrapped = "\n".join(f"    | {member}" for member in members)
    return f"  {field}{mark}:\n{wrapped};"


def _constraint_comment(schema: dict[str, Any]) -> str | None:
    """The constraints a TypeScript type cannot carry, written where a
    reader will see them.

    `media_sha256: string` is true and incomplete: the server will refuse
    anything that is not 64 lowercase hex characters, and a front end that
    builds one has to know that. TypeScript has no type for it, so the
    contract says it in the only place left.
    """
    stated = [
        f"{key}: {schema[key]}" for key in _DOCUMENTED_CONSTRAINTS if key in schema
    ]
    return f"  /** {', '.join(stated)} */" if stated else None


def _interface(name: str, node: dict[str, Any]) -> str:
    required = set(node.get("required", ()))
    lines = [f"export interface {name} {{"]
    for field, schema in node.get("properties", {}).items():
        comment = _constraint_comment(schema)
        if comment is not None:
            lines.append(comment)
        # Ruling 6: a `const` is always sent and is what a consumer narrows
        # on, so it is required whatever `required` says.
        optional = field not in required and "const" not in schema
        mark = "?" if optional else ""
        lines.append(_property(field, mark, _type_of(schema, f"{name}.{field}")))
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
