"""A small JSON Schema (2020-12) checker for the subset the xi-tools schema files use:
type, const, enum, required, properties, additionalProperties, items, minItems,
maxItems, minimum, maximum, pattern, anyOf and ``$ref`` (local ``#/$defs/…`` and sibling
``<file>.json#/$defs/…``). ``jsonschema`` isn't a dependency; this keeps a command's
real output honest against its schema file in the tests."""
import json
import re
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _resolve(ref: str, root: dict):
    file, _, frag = ref.partition("#")
    doc = load(file) if file else root
    node = doc
    for part in [p for p in frag.split("/") if p]:
        node = node[part]
    return node, doc


def errors(schema: dict, value, path: str = "$", root: dict | None = None) -> list:
    root = root or schema
    out = []
    if "$ref" in schema:
        sub, sub_root = _resolve(schema["$ref"], root)
        return errors(sub, value, path, sub_root)
    if "anyOf" in schema and all(errors(s, value, path, root) for s in schema["anyOf"]):
        out.append(f"{path}: matches none of anyOf")
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in ts):
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing {k!r}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                out += errors(props[k], v, f"{path}.{k}", root)
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected key {k!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                out += errors(schema["additionalProperties"], v, f"{path}.{k}", root)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                out += errors(schema["items"], v, f"{path}[{i}]", root)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        out.append(f"{path}: doesn't match {schema['pattern']}")
    return out
