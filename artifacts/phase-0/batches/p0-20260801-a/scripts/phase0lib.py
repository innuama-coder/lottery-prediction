"""Dependency-free validation primitives for Phase 0 evidence.

The schema validator intentionally implements the JSON Schema 2020-12 keywords
used by the frozen Phase 0 schemas. Unsupported assertion keywords fail closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class ValidationError(ValueError):
    """A deterministic validation failure with a JSON-path location."""


SUPPORTED_ASSERTIONS = {
    "$ref", "type", "const", "enum", "required", "properties",
    "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "format", "minimum", "maximum",
    "minProperties", "maxProperties", "allOf", "anyOf", "oneOf", "not",
}
ANNOTATIONS = {"$schema", "$id", "$defs", "title", "description", "default", "examples", "$comment"}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: invalid UTF-8 JSON: {exc}") from exc


def load_jsonl(path: Path) -> list[Any]:
    records: list[Any] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    raise ValidationError(f"{path}:{line_no}: blank JSONL lines are forbidden")
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValidationError(f"{path}:{line_no}: invalid JSON: {exc.msg}") from exc
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"{path}: cannot read UTF-8 JSONL: {exc}") from exc
    return records


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_non_jcs(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{path}: non-finite number is not valid JSON")
        raise ValidationError(f"{path}: floats are forbidden in Phase 0 canonical records; use decimal strings")
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ValidationError(f"{path}: unpaired Unicode surrogate is forbidden")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_jcs(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{path}: JSON object keys must be strings")
            _reject_non_jcs(key, f"{path}.<key>")
            _reject_non_jcs(item, f"{path}.{key}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the Phase 0 JCS-compatible encoding.

    Phase 0 forbids binary floating point, so the difficult ECMAScript number
    rendering portion of RFC 8785 is intentionally outside the data profile.
    Object keys are sorted by UTF-16 code units as required by JCS.
    """
    _reject_non_jcs(value)

    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int):
            return str(item)
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, list):
            return "[" + ",".join(encode(child) for child in item) + "]"
        if isinstance(item, dict):
            keys = sorted(item, key=lambda key: key.encode("utf-16-be"))
            return "{" + ",".join(f"{encode(key)}:{encode(item[key])}" for key in keys) + "}"
        raise ValidationError(f"unsupported JSON value: {type(item).__name__}")

    return encode(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def schemas_manifest_sha256(schema_dir: Path) -> str:
    """Hash schema names and exact bytes in a stable, unambiguous stream."""
    digest = hashlib.sha256()
    paths = sorted(schema_dir.glob("*.schema.json"), key=lambda p: p.name)
    if not paths:
        raise ValidationError(f"{schema_dir}: no *.schema.json files")
    for path in paths:
        name = path.name.encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValidationError(f"unsupported non-local $ref: {reference}")
    current: Any = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise ValidationError(f"unresolvable $ref: {reference}")
        current = current[token]
    if not isinstance(current, dict):
        raise ValidationError(f"$ref does not resolve to a schema object: {reference}")
    return current


def _is_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": (isinstance(value, (int, float)) and not isinstance(value, bool)),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _is_datetime(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
        return True
    except ValueError:
        return False


def validate_schema_instance(instance: Any, schema: dict[str, Any], *, root: dict[str, Any] | None = None, path: str = "$") -> None:
    root = schema if root is None else root
    unknown = set(schema) - SUPPORTED_ASSERTIONS - ANNOTATIONS
    if unknown:
        raise ValidationError(f"schema at {path} uses unsupported keywords: {sorted(unknown)}")
    if "$ref" in schema:
        validate_schema_instance(instance, _resolve_ref(root, schema["$ref"]), root=root, path=path)
        return
    for child in schema.get("allOf", []):
        validate_schema_instance(instance, child, root=root, path=path)
    if "anyOf" in schema:
        matches = sum(_schema_matches(instance, child, root, path) for child in schema["anyOf"])
        if matches == 0:
            raise ValidationError(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(_schema_matches(instance, child, root, path) for child in schema["oneOf"])
        if matches != 1:
            raise ValidationError(f"{path}: expected exactly one oneOf match, got {matches}")
    if "not" in schema and _schema_matches(instance, schema["not"], root, path):
        raise ValidationError(f"{path}: matches forbidden schema")
    if "const" in schema and instance != schema["const"]:
        raise ValidationError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise ValidationError(f"{path}: value is not in enum")
    if "type" in schema:
        types = [schema["type"]] if isinstance(schema["type"], str) else schema["type"]
        if not any(_is_type(instance, expected) for expected in types):
            raise ValidationError(f"{path}: expected type {types}, got {type(instance).__name__}")
    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise ValidationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate_schema_instance(value, properties[key], root=root, path=f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                raise ValidationError(f"{path}: unknown property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema_instance(value, schema["additionalProperties"], root=root, path=f"{path}.{key}")
        if len(instance) < schema.get("minProperties", 0):
            raise ValidationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise ValidationError(f"{path}: too many properties")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise ValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise ValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [canonical_json_bytes(item) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise ValidationError(f"{path}: duplicate array items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                validate_schema_instance(item, schema["items"], root=root, path=f"{path}[{index}]")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise ValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ValidationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ValidationError(f"{path}: string does not match pattern {schema['pattern']!r}")
        if schema.get("format") == "date-time" and not _is_datetime(instance):
            raise ValidationError(f"{path}: expected RFC 3339 UTC date-time with Z suffix")
        if "format" in schema and schema["format"] != "date-time":
            raise ValidationError(f"schema at {path} uses unsupported format {schema['format']!r}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ValidationError(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ValidationError(f"{path}: above maximum")


def _schema_matches(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> bool:
    try:
        validate_schema_instance(instance, schema, root=root, path=path)
        return True
    except ValidationError:
        return False


def lint_strict_schema(schema: dict[str, Any], *, source: str) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValidationError(f"{source}: must declare JSON Schema 2020-12")

    def walk(node: dict[str, Any], path: str) -> None:
        unknown = set(node) - SUPPORTED_ASSERTIONS - ANNOTATIONS
        if unknown:
            raise ValidationError(f"{source}{path}: unsupported keywords {sorted(unknown)}")
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            raise ValidationError(f"{source}{path}: object schema must set additionalProperties=false")
        for name, child in node.get("properties", {}).items():
            walk(child, f"{path}/properties/{name}")
        if isinstance(node.get("additionalProperties"), dict):
            walk(node["additionalProperties"], f"{path}/additionalProperties")
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}/items")
        for keyword in ("allOf", "anyOf", "oneOf"):
            for index, child in enumerate(node.get(keyword, [])):
                walk(child, f"{path}/{keyword}/{index}")
        if isinstance(node.get("not"), dict):
            walk(node["not"], f"{path}/not")
        for name, child in node.get("$defs", {}).items():
            walk(child, f"{path}/$defs/{name}")

    walk(schema, "#")


def validate_json_file(path: Path, schema_path: Path) -> Any:
    schema = load_json(schema_path)
    lint_strict_schema(schema, source=str(schema_path))
    instance = load_json(path)
    validate_schema_instance(instance, schema)
    return instance


def validate_jsonl_file(path: Path, schema_path: Path) -> list[Any]:
    schema = load_json(schema_path)
    lint_strict_schema(schema, source=str(schema_path))
    records = load_jsonl(path)
    for line_no, record in enumerate(records, 1):
        try:
            validate_schema_instance(record, schema)
        except ValidationError as exc:
            raise ValidationError(f"{path}:{line_no}: {exc}") from exc
    return records


def find_nulls(value: Any, path: str = "$") -> Iterable[str]:
    if value is None:
        yield path
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from find_nulls(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from find_nulls(child, f"{path}[{index}]")
