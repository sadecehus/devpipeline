"""Minimal JSON Schema (draft 2020-12 subset) validator — stdlib only.

Supported keywords: type, enum, const, properties, required,
additionalProperties (bool|schema), items, minItems, maxItems, uniqueItems,
minLength, maxLength, pattern, minimum, maximum, anyOf, oneOf, allOf, $ref
(local "#/$defs/x" and sibling files "name.schema.json[#/...]"). Annotation
keywords ($schema, $id, title, description, format) are ignored.

The subset is exactly what schemas/*.schema.json use; tests/unit/test_schemas.py
checks every schema keyword is supported so new schemas cannot silently skip
validation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import SCHEMAS

SUPPORTED = {
    "type", "enum", "const", "properties", "required", "additionalProperties", "items",
    "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
    "maximum", "anyOf", "oneOf", "allOf", "$ref", "$defs", "$schema", "$id", "title",
    "description", "format", "default", "examples",
}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

_cache: dict[str, dict] = {}


def load_schema(name: str) -> dict:
    if name not in _cache:
        path = SCHEMAS / name
        _cache[name] = json.loads(path.read_text(encoding="utf-8"))
    return _cache[name]


def _resolve(ref: str, root: dict) -> tuple[dict, dict]:
    file_part, _, pointer = ref.partition("#")
    target_root = load_schema(file_part) if file_part else root
    node: Any = target_root
    for part in [p for p in pointer.split("/") if p]:
        node = node[part]
    return node, target_root


def validate(instance: Any, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    root = root if root is not None else schema
    errors: list[str] = []

    if "$ref" in schema:
        target, target_root = _resolve(schema["$ref"], root)
        errors += validate(instance, target, target_root, path)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](instance) for t in types):
            return errors + [f"{path}: expected {'|'.join(types)}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in {schema['enum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = [json.dumps(i, sort_keys=True) for i in instance]
            if len(seen) != len(set(seen)):
                errors.append(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(instance):
                errors += validate(item, schema["items"], root, f"{path}[{i}]")

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required '{key}'")
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], root, f"{path}.{key}")
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    errors.append(f"{path}: unexpected property '{key}'")
                elif isinstance(extra, dict):
                    errors += validate(value, extra, root, f"{path}.{key}")

    for key in ("allOf",):
        for sub in schema.get(key, []):
            errors += validate(instance, sub, root, path)
    if "anyOf" in schema:
        if not any(not validate(instance, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(1 for s in schema["oneOf"] if not validate(instance, s, root, path))
        if matches != 1:
            errors.append(f"{path}: matches {matches} of oneOf (need exactly 1)")
    return errors


def validate_named(instance: Any, schema_name: str, sub: str | None = None) -> list[str]:
    root = load_schema(schema_name)
    schema = root
    if sub:
        schema, root = _resolve(f"{schema_name}#{sub}", root)
    return validate(instance, schema, root)


def unsupported_keywords(schema: Any, found: set | None = None) -> set:
    """Return keywords used in a schema that this validator does not implement."""
    found = found if found is not None else set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key not in SUPPORTED:
                found.add(key)
            if key in ("properties", "$defs"):
                for sub in value.values():
                    unsupported_keywords(sub, found)
            elif key in ("items", "additionalProperties") and isinstance(value, dict):
                unsupported_keywords(value, found)
            elif key in ("anyOf", "oneOf", "allOf"):
                for sub in value:
                    unsupported_keywords(sub, found)
    return found
