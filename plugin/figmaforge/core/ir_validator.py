"""
Dependency-free JSON-Schema (draft-07 subset) validator for the Design IR.

The repo is Python-stdlib-only (see CLAUDE.md), so this module implements the
small JSON-Schema subset the IR schema actually uses instead of pulling in
``jsonschema``. Supported keywords:

- ``type`` (string or list of strings)
- ``required``
- ``properties``
- ``items``
- ``enum`` / ``const``
- ``additionalProperties`` (boolean)
- ``minimum`` / ``maximum`` (numbers)
- ``minItems`` / ``maxItems`` (arrays)
- ``$ref`` (local ``#/definitions/<name>`` only)

Anything else is ignored, mirroring the "non-guessing" convention used across
FigmaForge: we validate what we can state and never invent requirements.

Errors are collected as a list of ``<path>: <message>`` strings so callers can
report them in bulk; ``ensure_valid`` raises :class:`IRValidationError` with
all of them joined.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "design-ir.schema.json"


class IRValidationError(ValueError):
    """Raised when IR data fails schema validation."""


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True  # unknown type keyword: do not reject


def _resolve_ref(schema: Dict[str, Any], ref: str) -> Optional[Dict[str, Any]]:
    if ref.startswith("#/definitions/"):
        return schema.get("definitions", {}).get(ref[len("#/definitions/"):])
    return None  # external refs are out of scope for this subset


def _validate(
    instance: Any,
    schema: Dict[str, Any],
    path: str,
    errors: List[str],
    root: Dict[str, Any],
) -> None:
    if not isinstance(schema, dict):
        return

    # Resolve local references before applying any keywords.
    ref = schema.get("$ref")
    if isinstance(ref, str):
        target = _resolve_ref(root, ref)
        if target is None:
            errors.append(f"{path}: unresolvable $ref {ref!r}")
            return
        schema = target

    # enum / const
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    # type
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_matches(instance, t) for t in expected):
            errors.append(f"{path}: expected type one of {expected}, got {type(instance).__name__}")
    elif isinstance(expected, str):
        if not _type_matches(instance, expected):
            errors.append(f"{path}: expected type {expected!r}, got {type(instance).__name__}")

    # numeric bounds
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    # object keywords
    if isinstance(instance, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, subschema in properties.items():
                if key in instance:
                    _validate(instance[key], subschema, f"{path}.{key}", errors, root)
        if schema.get("additionalProperties") is False:
            allowed = set(properties) if isinstance(properties, dict) else set()
            for key in instance:
                if key not in allowed:
                    errors.append(f"{path}: unexpected property {key!r}")

    # array keywords
    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                _validate(value, items, f"{path}[{index}]", errors, root)
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")


def validate(instance: Any, schema: Dict[str, Any]) -> List[str]:
    """Validate ``instance`` against ``schema``; return a list of error strings."""
    errors: List[str] = []
    _validate(instance, schema, "$", errors, schema)
    return errors


def load_schema(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load a JSON-Schema file (defaults to the IR schema shipped with the repo)."""
    schema_path = Path(path or DEFAULT_SCHEMA_PATH)
    with open(schema_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise IRValidationError(f"Schema {schema_path} must be a JSON object.")
    return data


def validate_ir(
    ir_dict: Dict[str, Any],
    schema: Optional[Dict[str, Any]] = None,
    schema_path: Optional[Path] = None,
) -> List[str]:
    """Validate a serialized IR dict; return a list of error strings.

    Either ``schema`` or ``schema_path`` may be given. When both are omitted,
    the repo's ``design-ir.schema.json`` is loaded automatically.
    """
    if schema is None:
        schema = load_schema(schema_path)
    return validate(ir_dict, schema)


def ensure_valid(
    ir_dict: Dict[str, Any],
    schema: Optional[Dict[str, Any]] = None,
    schema_path: Optional[Path] = None,
) -> None:
    """Validate and raise :class:`IRValidationError` on any failure."""
    errors = validate_ir(ir_dict, schema=schema, schema_path=schema_path)
    if errors:
        raise IRValidationError(
            "Design IR failed schema validation:\n  " + "\n  ".join(errors)
        )
