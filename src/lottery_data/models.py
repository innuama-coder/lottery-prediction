from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeAlias
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from .serialization import (
    canonical_json_bytes,
    core_fact_sha256,
    make_event_id,
    make_observation_id,
    make_revision_id,
    sha256_bytes,
)


JsonObject: TypeAlias = dict[str, Any]
SchemaName = Literal[
    "source-observation.schema.json",
    "draw-record.schema.json",
    "dataset-release.schema.json",
    "run-manifest.schema.json",
    "run-event.schema.json",
    "run-result.schema.json",
    "run-manifest-v1.1.schema.json",
    "run-event-v1.1.schema.json",
    "run-manifest-v1.2.schema.json",
    "run-event-v1.2.schema.json",
    "run-manifest-v1.3.schema.json",
    "run-event-v1.3.schema.json",
]

_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "phase1"
_SCHEMA_ALIASES = {
    "SourceObservation": "source-observation.schema.json",
    "DrawRecord": "draw-record.schema.json",
    "DatasetRelease": "dataset-release.schema.json",
    "RunManifest": "run-manifest.schema.json",
    "RunEvent": "run-event.schema.json",
    "RunResult": "run-result.schema.json",
    "RunManifestV1.1": "run-manifest-v1.1.schema.json",
    "RunEventV1.1": "run-event-v1.1.schema.json",
    "RunManifestV1.2": "run-manifest-v1.2.schema.json",
    "RunEventV1.2": "run-event-v1.2.schema.json",
    "RunManifestV1.3": "run-manifest-v1.3.schema.json",
    "RunEventV1.3": "run-event-v1.3.schema.json",
}
_SCHEMA_FILES = frozenset(_SCHEMA_ALIASES.values())


class ContractViolation(ValueError):
    def __init__(self, schema_name: str, violations: str | list[str]) -> None:
        self.schema_name = schema_name
        self.violations = (violations,) if isinstance(violations, str) else tuple(violations)
        super().__init__(f"{schema_name}: " + "; ".join(self.violations))


def _normalize_schema_name(schema_name: SchemaName | str) -> str:
    normalized = _SCHEMA_ALIASES.get(schema_name, schema_name)
    if normalized not in _SCHEMA_FILES:
        raise ContractViolation(str(schema_name), "unknown Phase 1 schema")
    return normalized


def _schema_name_for_value(schema_name: SchemaName | str, value: Any) -> str:
    """Route only the public aliases by their explicit object version.

    Explicit schema filenames remain explicit. This preserves the frozen v1
    validation surface while allowing callers of ``RunManifest``/``RunEvent``
    to consume the separately versioned live profile.
    """
    if schema_name == "RunManifest" and isinstance(value, dict) and value.get("run_schema_version") == "1.1.0":
        return "run-manifest-v1.1.schema.json"
    if schema_name == "RunManifest" and isinstance(value, dict) and value.get("run_schema_version") == "1.2.0":
        return "run-manifest-v1.2.schema.json"
    if schema_name == "RunManifest" and isinstance(value, dict) and value.get("run_schema_version") == "1.3.0":
        return "run-manifest-v1.3.schema.json"
    if schema_name == "RunEvent" and isinstance(value, dict) and value.get("event_schema_version") == "1.1.0":
        return "run-event-v1.1.schema.json"
    if schema_name == "RunEvent" and isinstance(value, dict) and value.get("event_schema_version") == "1.2.0":
        return "run-event-v1.2.schema.json"
    if schema_name == "RunEvent" and isinstance(value, dict) and value.get("event_schema_version") == "1.3.0":
        return "run-event-v1.3.schema.json"
    return _normalize_schema_name(schema_name)


def distribution_file_by_suffix(suffix: str) -> Path:
    """Locate one installed project file by its unique RECORD suffix."""
    normalized = PurePosixPath(suffix).as_posix()
    if not suffix or normalized != suffix or normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        raise ContractViolation("distribution-file", f"invalid distribution suffix: {suffix!r}")
    try:
        installed = distribution("autoresearch-lotte")
    except PackageNotFoundError as exc:
        raise ContractViolation("distribution-file", "distribution autoresearch-lotte is not installed") from exc
    matches = []
    for package_path in installed.files or ():
        candidate = PurePosixPath(str(package_path).replace("\\", "/")).as_posix()
        if candidate == normalized or candidate.endswith("/" + normalized):
            matches.append(package_path)
    if len(matches) != 1:
        raise ContractViolation(
            "distribution-file",
            f"expected exactly one installed file ending with {normalized!r}; found {len(matches)}",
        )
    package_path = matches[0]
    try:
        located = Path(package_path.locate())
    except (AttributeError, OSError) as exc:
        raise ContractViolation("distribution-file", f"cannot locate installed RECORD entry: {package_path}") from exc
    if not located.is_file():
        raise ContractViolation("distribution-file", f"installed RECORD entry is not a file: {located}")
    return located


def schema_path(schema_name: SchemaName | str) -> Path:
    normalized = _normalize_schema_name(schema_name)
    source_path = _SCHEMA_ROOT / normalized
    if source_path.is_file():
        return source_path
    relative = Path("share") / "autoresearch-lotte" / "schemas" / "phase1" / normalized
    return distribution_file_by_suffix(relative.as_posix())


@lru_cache(maxsize=8)
def _validator(schema_name: str) -> Draft202012Validator:
    path = schema_path(schema_name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _json_path(error: Any) -> str:
    suffix = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
    return "$" + suffix


def validate_schema(schema_name: SchemaName | str, value: JsonObject) -> JsonObject:
    normalized = _schema_name_for_value(schema_name, value)
    if not isinstance(value, dict):
        raise ContractViolation(normalized, "$: expected object")
    errors = sorted(_validator(normalized).iter_errors(value), key=lambda error: (_json_path(error), error.message))
    if errors:
        raise ContractViolation(normalized, [f"{_json_path(error)}: {error.message}" for error in errors])
    return value


def _strictly_increasing(numbers: Any) -> bool:
    return isinstance(numbers, list) and all(
        isinstance(number, int) and not isinstance(number, bool) for number in numbers
    ) and all(left < right for left, right in zip(numbers, numbers[1:]))


def _check_number_order(value: JsonObject, violations: list[str]) -> None:
    if not _strictly_increasing(value["front_numbers"]):
        violations.append("front_numbers must be strictly increasing unique integers")
    if not _strictly_increasing(value["back_numbers"]):
        violations.append("back_numbers must be strictly increasing unique integers")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def validate_semantics(
    schema_name: SchemaName | str,
    value: JsonObject,
) -> JsonObject:
    normalized = _schema_name_for_value(schema_name, value)
    violations: list[str] = []

    if normalized in {"source-observation.schema.json", "draw-record.schema.json"}:
        _check_number_order(value, violations)
        if value["core_fact_sha256"] != core_fact_sha256(value):
            violations.append("core_fact_sha256 mismatch")

    if normalized == "source-observation.schema.json":
        expected = make_observation_id(
            value["source_id"], value["game"], value["issue_id"], value["raw_sha256"], value["parser_version"]
        )
        if value["observation_id"] != expected:
            violations.append("observation_id mismatch")

    elif normalized == "draw-record.schema.json":
        links = value["evidence_links"]
        if len({link["publisher_id"] for link in links}) != 2:
            violations.append("evidence_links require two distinct publishers")
        if len({(link["publisher_id"], link["observation_id"]) for link in links}) != 2:
            violations.append("evidence publisher/observation pairs must be unique")
        expected = make_revision_id(
            value["game"], value["issue_id"], value["core_fact_sha256"], value["supersedes_revision_id"]
        )
        if value["revision_id"] != expected:
            violations.append("revision_id mismatch")

    elif normalized in {"run-manifest.schema.json", "run-manifest-v1.1.schema.json", "run-manifest-v1.2.schema.json", "run-manifest-v1.3.schema.json"}:
        requests = value["request_plan"]
        request_ids = [request["request_id"] for request in requests]
        sequences = [request["sequence"] for request in requests]
        if len(request_ids) != len(set(request_ids)):
            violations.append("request_plan request_id values must be unique")
        if sequences != list(range(1, len(requests) + 1)):
            violations.append("request_plan sequence must be contiguous from 1 and in file order")
        snapshot_only = value["source_mode"] == "snapshot" or value["replay_of_run_id"] is not None
        if snapshot_only:
            for request in requests:
                if request["method"] != "SNAPSHOT" or not request.get("input_ref"):
                    violations.append(f"{request['request_id']}: snapshot/replay request requires SNAPSHOT and input_ref")
                    continue
        if normalized == "run-manifest-v1.1.schema.json":
            expected = _LIVE_STATIC_REQUESTS
            for index, request in enumerate(requests):
                identity = {key: request.get(key) for key in _LIVE_REQUEST_IDENTITY_FIELDS}
                expected_identity = {key: expected[index].get(key) for key in _LIVE_REQUEST_IDENTITY_FIELDS}
                if identity != expected_identity:
                    violations.append(f"request_plan[{index}] differs from the frozen live request identity")
            if requests[3].get("child_authorization") != _LIVE_CHILD_AUTHORIZATION:
                violations.append("GD discovery child_authorization differs from the bounded live profile")
            if any("input_ref" in request for request in requests):
                violations.append("live GET request_plan entries must not contain input_ref")
        elif normalized in {"run-manifest-v1.2.schema.json", "run-manifest-v1.3.schema.json"}:
            for index, request in enumerate(requests):
                if request != _LIVE_V12_STATIC_REQUESTS[index]:
                    violations.append(f"request_plan[{index}] differs from the frozen live v1.2 request identity")
            if any("input_ref" in request or "child_authorization" in request for request in requests):
                violations.append("live v1.2 requests must not contain input_ref or child_authorization")

    elif normalized == "dataset-release.schema.json":
        # Non-negative per-game counts are enforced by the Schema. This object
        # has no total-record field, so equality with actual draws/observations
        # is intentionally a cross-file validation responsibility, not claimed
        # by this single-object semantic layer.
        pass

    elif normalized in {"run-event.schema.json", "run-event-v1.1.schema.json", "run-event-v1.2.schema.json", "run-event-v1.3.schema.json"}:
        expected = make_event_id(
            value["run_id"], value["sequence"], value["event_type"], value["request_id"], value["attempt"]
        )
        if value["event_id"] != expected:
            violations.append("event_id mismatch")
        if normalized != "run-event-v1.3.schema.json" and value["event_type"] in {"request_started", "request_succeeded", "request_failed"} and value["attempt"] != 1:
            violations.append("Phase 1 request attempt must equal 1")
        if normalized == "run-event-v1.3.schema.json" and value["event_type"] in {"request_started", "request_succeeded", "request_failed"} and value["attempt"] not in {1, 2}:
            violations.append("live v1.3 request attempt must equal 1 or 2")
        if normalized == "run-event-v1.1.schema.json":
            if value["event_type"] == "request_discovered":
                if value["authorization_sha256"] != make_live_child_authorization_sha256(value):
                    violations.append("authorization_sha256 mismatch")
            if value["event_type"] == "request_succeeded":
                raw_digest = _content_addressed_raw_digest(value.get("artifact_ref"))
                if raw_digest is None:
                    violations.append("live request_succeeded artifact_ref must be content-addressed")
        elif normalized in {"run-event-v1.2.schema.json", "run-event-v1.3.schema.json"} and value["event_type"] == "request_succeeded":
            if _content_addressed_raw_digest(value.get("artifact_ref")) is None:
                violations.append("live request_succeeded artifact_ref must be content-addressed")

    elif normalized == "run-result.schema.json":
        request_stats = value["request_stats"]
        if request_stats["started"] != request_stats["succeeded"] + request_stats["failed"]:
            violations.append("request count mismatch: started != succeeded + failed")
        if request_stats["planned"] != request_stats["started"] + request_stats["not_started"]:
            violations.append("request count mismatch: planned != started + not_started")
        if _parse_utc(value["completed_at_utc"]) < _parse_utc(value["started_at_utc"]):
            violations.append("completed_at_utc precedes started_at_utc")
        if value["status"] == "published":
            blocking = {
                "request.failed": request_stats["failed"],
                **{f"observation.{key}": value["observation_stats"][key] for key in ("invalid", "missing", "duplicate", "conflict")},
                **{f"change.{key}": value["change_stats"][key] for key in ("conflict", "invalid", "duplicate", "manual_core_edit")},
                "candidate.unresolved": value["candidate_stats"]["unresolved"],
            }
            nonzero = sorted(key for key, count in blocking.items() if count != 0)
            if nonzero:
                violations.append(f"published result has blocking counters: {nonzero}")

    if violations:
        raise ContractViolation(normalized, violations)
    return value


def validate_object(
    schema_name: SchemaName | str,
    value: JsonObject,
) -> JsonObject:
    validate_schema(schema_name, value)
    return validate_semantics(schema_name, value)


_LIVE_REQUEST_IDENTITY_FIELDS = (
    "request_id", "sequence", "source_id", "publisher_id", "game", "method", "url",
    "request_kind", "parser_id", "parser_version",
)

_LIVE_STATIC_REQUESTS: tuple[JsonObject, ...] = (
    {
        "request_id": "live-ydniu-ssq-history", "sequence": 1, "source_id": "ydniu",
        "publisher_id": "ydniu-publisher", "game": "ssq", "method": "GET",
        "url": "https://www.ydniu.com/open/ssq-500/1.html", "request_kind": "history",
        "parser_id": "phase1-ydniu-parser", "parser_version": "1.0.0",
    },
    {
        "request_id": "live-swlc-ssq-history", "sequence": 2, "source_id": "swlc",
        "publisher_id": "swlc-publisher", "game": "ssq", "method": "GET",
        "url": "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30",
        "request_kind": "history", "parser_id": "phase1-swlc-live-parser", "parser_version": "1.0.0",
    },
    {
        "request_id": "live-ydniu-dlt-history", "sequence": 3, "source_id": "ydniu",
        "publisher_id": "ydniu-publisher", "game": "dlt", "method": "GET",
        "url": "https://www.ydniu.com/open/dlt-500/1.html", "request_kind": "history",
        "parser_id": "phase1-ydniu-parser", "parser_version": "1.0.0",
    },
    {
        "request_id": "live-gdlottery-dlt-discovery", "sequence": 4, "source_id": "gdlottery",
        "publisher_id": "gdlottery-publisher", "game": "dlt", "method": "GET",
        "url": "https://www.gdlottery.cn/html/dlt/index.html", "request_kind": "discovery",
        "parser_id": "phase1-gdlottery-live-parser", "parser_version": "2.0.0",
    },
)

_LIVE_CHILD_AUTHORIZATION: JsonObject = {
    "child_request_id": "live-gdlottery-dlt-announcement",
    "source_id": "gdlottery",
    "publisher_id": "gdlottery-publisher",
    "game": "dlt",
    "method": "GET",
    "request_kind": "announcement",
    "parser_id": "phase1-gdlottery-live-parser",
    "parser_version": "2.0.0",
    "same_origin": "https://www.gdlottery.cn",
    "path_pattern": r"^/f_html/kjgg/P085_[0-9]{5}\.html$",
    "max_children": 1,
}

_LIVE_V12_HTML_RESPONSE_PROFILE: JsonObject = {
    "expected_media_type": "text/html",
    "max_response_bytes": 1048576,
}

_LIVE_V12_STATIC_REQUESTS: tuple[JsonObject, ...] = (
    {
        "request_id": "live-ydniu-ssq-history", "sequence": 1, "source_id": "ydniu",
        "publisher_id": "ydniu-publisher", "game": "ssq", "method": "GET",
        "url": "https://www.ydniu.com/open/ssq-500/1.html", "request_kind": "history",
        "parser_id": "phase1-ydniu-parser", "parser_version": "1.0.0",
        "response_profile": dict(_LIVE_V12_HTML_RESPONSE_PROFILE),
    },
    {
        "request_id": "live-swlc-ssq-history", "sequence": 2, "source_id": "swlc",
        "publisher_id": "swlc-publisher", "game": "ssq", "method": "GET",
        "url": "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30",
        "request_kind": "history", "parser_id": "phase1-swlc-live-parser", "parser_version": "1.0.0",
        "response_profile": dict(_LIVE_V12_HTML_RESPONSE_PROFILE),
    },
    {
        "request_id": "live-ydniu-dlt-history", "sequence": 3, "source_id": "ydniu",
        "publisher_id": "ydniu-publisher", "game": "dlt", "method": "GET",
        "url": "https://www.ydniu.com/open/dlt-500/1.html", "request_kind": "history",
        "parser_id": "phase1-ydniu-parser", "parser_version": "1.0.0",
        "response_profile": dict(_LIVE_V12_HTML_RESPONSE_PROFILE),
    },
    {
        "request_id": "live-gdlottery-dlt-history", "sequence": 4, "source_id": "gdlottery",
        "publisher_id": "gdlottery-publisher", "game": "dlt", "method": "GET",
        "url": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json", "request_kind": "history",
        "parser_id": "phase1-gdlottery-history-parser", "parser_version": "1.0.0",
        "response_profile": {"expected_media_type": "application/json", "max_response_bytes": 2097152},
    },
)

LIVE_AUTHORIZATION_CANONICAL_FIELDS = (
    "parent_request_id", "discovery_request_id", "discovery_raw_ref", "discovery_raw_sha256",
    "request_id", "source_id", "publisher_id", "game", "method", "request_kind", "url",
    "expected_raw_issue_id", "parser_id", "parser_version",
)

_CONTENT_ADDRESSED_RAW_RE = re.compile(
    r"^raw/[a-z0-9][a-z0-9._-]*/(?:ssq|dlt)/sha256/([0-9a-f]{64})\.raw$"
)


def make_live_child_authorization_sha256(value: JsonObject) -> str:
    """Hash the exact discovered-child authorization closure.

    The projection field set and canonical-json-v1 serialization are part of
    contract 3.2.0; extra event metadata cannot silently alter this identity.
    """
    missing = [field for field in LIVE_AUTHORIZATION_CANONICAL_FIELDS if field not in value]
    if missing:
        raise ContractViolation("live-child-authorization", f"missing canonical fields: {missing}")
    projection = {field: value[field] for field in LIVE_AUTHORIZATION_CANONICAL_FIELDS}
    return sha256_bytes(canonical_json_bytes(projection))


def _content_addressed_raw_digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _CONTENT_ADDRESSED_RAW_RE.fullmatch(value)
    return match.group(1) if match else None


def _validate_discovered_child(event: JsonObject, parent_success: JsonObject, authorization: JsonObject) -> list[str]:
    violations: list[str] = []
    expected_identity = {
        "request_id": authorization["child_request_id"],
        **{key: authorization[key] for key in (
            "source_id", "publisher_id", "game", "method", "request_kind", "parser_id", "parser_version"
        )},
    }
    if any(event.get(key) != expected for key, expected in expected_identity.items()):
        violations.append("discovered child identity exceeds the manifest authorization")
    if event.get("parent_request_id") != "live-gdlottery-dlt-discovery" or event.get("discovery_request_id") != event.get("parent_request_id"):
        violations.append("discovered child parent/discovery request is not the authorized GD discovery")
    if parent_success.get("artifact_ref") != event.get("discovery_raw_ref"):
        violations.append("discovered child does not bind the successful discovery raw artifact")
    digest = _content_addressed_raw_digest(event.get("discovery_raw_ref"))
    if digest is None or digest != event.get("discovery_raw_sha256"):
        violations.append("discovery raw ref and SHA-256 are not one content-addressed identity")

    split = urlsplit(event.get("url", ""))
    try:
        port = split.port
    except ValueError:
        port = -1
    actual_origin = f"{split.scheme}://{split.netloc}"
    if (
        actual_origin != authorization["same_origin"] or split.username is not None or split.password is not None
        or port is not None or split.query or split.fragment or not re.fullmatch(authorization["path_pattern"], split.path)
    ):
        violations.append("discovered URL is outside the same-origin/path authorization")
    issue_match = re.fullmatch(r"/f_html/kjgg/P085_([0-9]{5})\.html", split.path)
    if issue_match is None or "20" + issue_match.group(1) != event.get("expected_raw_issue_id"):
        violations.append("discovered URL issue does not equal expected_raw_issue_id")
    if event.get("authorization_sha256") != make_live_child_authorization_sha256(event):
        violations.append("discovered child authorization hash mismatch")
    return violations


def _validate_live_event_stream_v13(
    manifest: JsonObject,
    events: list[JsonObject],
    run_result: JsonObject | None = None,
) -> JsonObject:
    """Validate four logical requests with at most two fully audited attempts."""
    validate_object("RunManifestV1.3", manifest)
    if not isinstance(events, list) or not events:
        raise ContractViolation("live-event-stream-v1.3", "events must be a non-empty list")
    plan = {request["request_id"]: request for request in manifest["request_plan"]}
    state = {request_id: "planned" for request_id in plan}
    current_attempt = {request_id: 0 for request_id in plan}
    terminal_types = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    terminal_type: str | None = None
    violations: list[str] = []
    if [(row.get("event_type"), row.get("request_id")) for row in events[:2]] != [
        ("run_planned", None), ("run_started", None),
    ]:
        violations.append("live v1.3 stream must begin with run_planned, run_started")

    for index, event in enumerate(events, start=1):
        try:
            validate_object("RunEventV1.3", event)
        except ContractViolation as exc:
            violations.extend(f"event[{index}]: {item}" for item in exc.violations)
            continue
        if event["sequence"] != index or event["run_id"] != manifest["run_id"]:
            violations.append(f"event[{index}]: sequence/run identity differs")
        event_type, request_id = event["event_type"], event["request_id"]
        if event_type in terminal_types:
            if terminal_type is not None or index != len(events):
                violations.append("run terminal event must occur once and be final")
            terminal_type = event_type
            continue
        if event_type not in {"request_started", "request_succeeded", "request_failed"}:
            continue
        request = plan.get(request_id)
        if request is None:
            violations.append(f"unplanned request event rejected: {request_id}")
            continue
        if event.get("source_id") != request["source_id"] or event.get("game") != request["game"]:
            violations.append(f"{request_id}: request event identity differs from plan")
            continue
        attempt = event["attempt"]
        if event_type == "request_started":
            expected = current_attempt[request_id] + 1
            allowed_state = "planned" if attempt == 1 else "retry_pending"
            if attempt != expected or state[request_id] != allowed_state:
                violations.append(f"{request_id}: attempt {attempt} is not the next legal attempt")
            else:
                current_attempt[request_id] = attempt
                state[request_id] = "started"
        elif state[request_id] != "started" or attempt != current_attempt[request_id]:
            violations.append(f"{request_id}: terminal event does not close its started attempt")
        elif event_type == "request_succeeded":
            state[request_id] = "succeeded"
        elif attempt == 1 and event.get("error_code") == "DNS_TIMEOUT_TLS_OR_REQUIRED_SOURCE_UNAVAILABLE":
            state[request_id] = "retry_pending"
        else:
            state[request_id] = "failed"

    if terminal_type is None and run_result is not None:
        violations.append("RunResult is forbidden before a run terminal event")
    if terminal_type is not None and run_result is None:
        violations.append("terminal live v1.3 stream requires RunResult")
    if terminal_type in {"run_published", "run_no_change"} and any(value != "succeeded" for value in state.values()):
        violations.append(f"{terminal_type} requires every logical request to succeed")
    if terminal_type == "run_rejected" and any(value in {"started", "retry_pending"} for value in state.values()):
        violations.append("run_rejected cannot leave an attempt open or a retry pending")
    if terminal_type is not None and any(value == "started" for value in state.values()):
        violations.append("terminal stream cannot leave a started attempt open")

    started = sum(value != "planned" for value in state.values())
    succeeded = sum(value == "succeeded" for value in state.values())
    failed_states = {"failed", "retry_pending"} if terminal_type == "run_interrupted" else {"failed"}
    failed = sum(value in failed_states for value in state.values())
    not_started = sum(value == "planned" for value in state.values())
    if run_result is not None:
        try:
            validate_object("RunResult", run_result)
        except ContractViolation as exc:
            violations.extend(f"run_result: {item}" for item in exc.violations)
        else:
            expected = {"planned": 4, "started": started, "succeeded": succeeded, "failed": failed, "not_started": not_started}
            if run_result["request_stats"] != expected:
                violations.append(f"RunResult request_stats differ from logical requests: expected {expected}")
            expected_status = {
                "run_published": "published", "run_no_change": "no_change",
                "run_rejected": "rejected", "run_interrupted": "interrupted",
            }.get(terminal_type)
            if expected_status is not None and run_result["status"] != expected_status:
                violations.append("RunResult status differs from terminal event")
    if violations:
        raise ContractViolation("live-event-stream-v1.3", violations)
    return {"effective_request_ids": list(plan), "request_stats": {
        "planned": 4, "started": started, "succeeded": succeeded,
        "failed": failed, "not_started": not_started,
    }}


def validate_live_event_stream(
    manifest: JsonObject,
    events: list[JsonObject],
    run_result: JsonObject | None = None,
) -> JsonObject:
    """Validate a live v1.1 append-only event prefix or terminal stream.

    Effective plan = four immutable manifest requests plus the one child whose
    discovery event closes over the persisted discovery raw and authorization.
    A non-terminal online prefix may omit RunResult and leave a request started;
    a run terminal makes the stream complete and requires its matching RunResult.
    """
    validate_object("RunManifestV1.1", manifest)
    violations: list[str] = []
    if not isinstance(events, list) or not events:
        raise ContractViolation("live-event-stream", "events must be a non-empty list")

    effective = {request["request_id"]: request for request in manifest["request_plan"]}
    lifecycle: dict[str, str] = {request_id: "planned" for request_id in effective}
    succeeded_events: dict[str, JsonObject] = {}
    discovered = False
    started = succeeded = failed = 0
    terminal_run_types = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    terminal_run_events = 0
    terminal_run_type: str | None = None

    for index, event in enumerate(events, start=1):
        try:
            validate_object("RunEventV1.1", event)
        except ContractViolation as exc:
            violations.extend(f"event[{index}]: {item}" for item in exc.violations)
            continue
        if event["sequence"] != index:
            violations.append(f"event[{index}]: sequence must be contiguous from 1")
        if event["run_id"] != manifest["run_id"]:
            violations.append(f"event[{index}]: run_id differs from manifest")

        event_type = event["event_type"]
        request_id = event["request_id"]
        if event_type == "request_discovered":
            if discovered:
                violations.append("request_discovered may occur only once")
                continue
            parent_id = event.get("parent_request_id")
            parent_success = succeeded_events.get(parent_id)
            if parent_success is None:
                violations.append("request_discovered must follow the discovery request_succeeded event")
                continue
            authorization = manifest["request_plan"][3]["child_authorization"]
            violations.extend(_validate_discovered_child(event, parent_success, authorization))
            if request_id in effective:
                violations.append("discovered request_id collides with the effective plan")
                continue
            effective[request_id] = {
                key: event[key] for key in (
                    "request_id", "source_id", "publisher_id", "game", "method", "url",
                    "request_kind", "parser_id", "parser_version"
                )
            }
            lifecycle[request_id] = "planned"
            discovered = True
        elif event_type == "request_started":
            if request_id not in effective:
                violations.append(f"unplanned request_started rejected: {request_id}")
            elif any(event.get(key) != effective[request_id].get(key) for key in ("source_id", "game")):
                violations.append(f"{request_id}: request_started identity differs from the effective plan")
            elif lifecycle[request_id] != "planned":
                violations.append(f"{request_id}: request_started is not a unique planned transition")
            else:
                lifecycle[request_id] = "started"
                started += 1
        elif event_type in {"request_succeeded", "request_failed"}:
            if request_id not in effective:
                violations.append(f"unplanned terminal request event rejected: {request_id}")
            elif any(event.get(key) != effective[request_id].get(key) for key in ("source_id", "game")):
                violations.append(f"{request_id}: terminal request identity differs from the effective plan")
            elif lifecycle[request_id] != "started":
                violations.append(f"{request_id}: terminal event lacks exactly one preceding request_started")
            else:
                lifecycle[request_id] = "succeeded" if event_type == "request_succeeded" else "failed"
                if event_type == "request_succeeded":
                    succeeded += 1
                    succeeded_events[request_id] = event
                else:
                    failed += 1
        if event_type in terminal_run_types:
            terminal_run_events += 1
            terminal_run_type = event_type
            if index != len(events):
                violations.append("run terminal event must be the final event")

    dangling = sorted(request_id for request_id, state in lifecycle.items() if state == "started")
    if terminal_run_events > 1:
        violations.append("live stream permits at most one final run terminal event")
    if terminal_run_events == 1 and dangling:
        violations.append(f"started requests lack one terminal event: {dangling}")
    if terminal_run_events == 1 and run_result is None:
        violations.append(f"terminal event {terminal_run_type} requires one matching RunResult")
    if terminal_run_events == 0 and run_result is not None:
        violations.append("RunResult is forbidden before a run terminal event")

    not_started = sum(state == "planned" for state in lifecycle.values())
    if terminal_run_type in {"run_published", "run_no_change"}:
        successful_states = len(effective) == 5 and discovered and all(state == "succeeded" for state in lifecycle.values())
        if not successful_states or (started, succeeded, failed, not_started) != (5, 5, 0, 0):
            violations.append(
                f"{terminal_run_type} requires one authorized discovered child and all five effective requests succeeded"
            )

    if run_result is not None:
        try:
            validate_object("RunResult", run_result)
        except ContractViolation as exc:
            violations.extend(f"run_result: {item}" for item in exc.violations)
        else:
            stats = run_result["request_stats"]
            expected_stats = {
                "planned": len(effective), "started": started, "succeeded": succeeded,
                "failed": failed, "not_started": not_started,
            }
            if any(stats[key] != count for key, count in expected_stats.items()):
                violations.append(f"RunResult request_stats differ from effective plan/events: expected {expected_stats}")
            if run_result["run_id"] != manifest["run_id"] or run_result["mode"] != "incremental":
                violations.append("RunResult run_id/mode differ from the live manifest")
            terminal_status = {
                "run_published": "published",
                "run_no_change": "no_change",
                "run_rejected": "rejected",
                "run_interrupted": "interrupted",
            }.get(terminal_run_type)
            if terminal_status is not None and run_result["status"] != terminal_status:
                violations.append(
                    f"RunResult status {run_result['status']!r} does not map to terminal event {terminal_run_type!r}"
                )
            if terminal_run_type == "run_published":
                if run_result["exit_code"] != 0 or run_result["release_id"] is None:
                    violations.append("run_published requires RunResult published/exit 0/non-null release_id")
            elif terminal_run_type == "run_no_change":
                if run_result["exit_code"] != 0 or run_result["release_id"] is not None:
                    violations.append("run_no_change requires RunResult no_change/exit 0/null release_id")
            elif terminal_run_type == "run_rejected":
                if run_result["exit_code"] in {None, 0} or run_result["release_id"] is not None:
                    violations.append("run_rejected requires RunResult rejected/non-zero exit/null release_id")
            elif terminal_run_type == "run_interrupted":
                if run_result["exit_code"] is not None or run_result["release_id"] is not None:
                    violations.append("run_interrupted requires RunResult interrupted/null exit/null release_id")

    if violations:
        raise ContractViolation("live-event-stream", violations)
    return {
        "effective_request_ids": list(effective),
        "request_stats": {
            "planned": len(effective), "started": started, "succeeded": succeeded,
            "failed": failed, "not_started": not_started,
        },
    }


# Keep the v1.1 validator as the immutable legacy implementation above.  The
# public name below only dispatches by the manifest's explicit profile version.
_validate_live_event_stream_v11 = validate_live_event_stream


def _validate_live_event_stream_v12(
    manifest: JsonObject,
    events: list[JsonObject],
    run_result: JsonObject | None = None,
) -> JsonObject:
    """Validate the four-static-request live v1.2 event profile."""
    validate_object("RunManifestV1.2", manifest)
    if not isinstance(events, list) or not events:
        raise ContractViolation("live-event-stream-v1.2", "events must be a non-empty list")

    effective = {request["request_id"]: request for request in manifest["request_plan"]}
    lifecycle = {request_id: "planned" for request_id in effective}
    terminal_types = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    terminal_count = 0
    terminal_type: str | None = None
    started = succeeded = failed = 0
    violations: list[str] = []

    for index, event in enumerate(events, start=1):
        try:
            validate_object("RunEventV1.2", event)
        except ContractViolation as exc:
            violations.extend(f"event[{index}]: {item}" for item in exc.violations)
            continue
        if event["sequence"] != index:
            violations.append(f"event[{index}]: sequence must be contiguous from 1")
        if event["run_id"] != manifest["run_id"]:
            violations.append(f"event[{index}]: run_id differs from manifest")

        event_type = event["event_type"]
        request_id = event["request_id"]
        if event_type == "request_started":
            request = effective.get(request_id)
            if request is None:
                violations.append(f"unplanned request_started rejected: {request_id}")
            elif any(event.get(key) != request[key] for key in ("source_id", "game")):
                violations.append(f"{request_id}: request_started identity differs from the static plan")
            elif lifecycle[request_id] != "planned":
                violations.append(f"{request_id}: request_started is not a unique planned transition")
            else:
                lifecycle[request_id] = "started"
                started += 1
        elif event_type in {"request_succeeded", "request_failed"}:
            request = effective.get(request_id)
            if request is None:
                violations.append(f"unplanned terminal request event rejected: {request_id}")
            elif any(event.get(key) != request[key] for key in ("source_id", "game")):
                violations.append(f"{request_id}: terminal request identity differs from the static plan")
            elif lifecycle[request_id] != "started":
                violations.append(f"{request_id}: terminal event lacks exactly one preceding request_started")
            else:
                lifecycle[request_id] = "succeeded" if event_type == "request_succeeded" else "failed"
                if event_type == "request_succeeded":
                    succeeded += 1
                else:
                    failed += 1
        if event_type in terminal_types:
            terminal_count += 1
            terminal_type = event_type
            if index != len(events):
                violations.append("run terminal event must be the final event")

    dangling = sorted(request_id for request_id, state in lifecycle.items() if state == "started")
    if terminal_count > 1:
        violations.append("live stream permits at most one final run terminal event")
    if terminal_count == 1 and dangling:
        violations.append(f"started requests lack one terminal event: {dangling}")
    if terminal_count == 1 and run_result is None:
        violations.append(f"terminal event {terminal_type} requires one matching RunResult")
    if terminal_count == 0 and run_result is not None:
        violations.append("RunResult is forbidden before a run terminal event")

    not_started = sum(state == "planned" for state in lifecycle.values())
    if terminal_type in {"run_published", "run_no_change"}:
        expected_success_events: list[tuple[str, str | None]] = [("run_planned", None), ("run_started", None)]
        for request in manifest["request_plan"]:
            expected_success_events.extend([
                ("request_started", request["request_id"]),
                ("request_succeeded", request["request_id"]),
            ])
        expected_success_events.append((terminal_type, None))
        actual_success_events = [(event.get("event_type"), event.get("request_id")) for event in events]
        if actual_success_events != expected_success_events:
            violations.append(f"{terminal_type} requires the exact ordered four-request success stream")
        if (started, succeeded, failed, not_started) != (4, 4, 0, 0):
            violations.append(f"{terminal_type} requires all four static requests succeeded")

    if run_result is not None:
        try:
            validate_object("RunResult", run_result)
        except ContractViolation as exc:
            violations.extend(f"run_result: {item}" for item in exc.violations)
        else:
            expected_stats = {
                "planned": 4, "started": started, "succeeded": succeeded,
                "failed": failed, "not_started": not_started,
            }
            if any(run_result["request_stats"][key] != count for key, count in expected_stats.items()):
                violations.append(f"RunResult request_stats differ from static plan/events: expected {expected_stats}")
            if run_result["run_id"] != manifest["run_id"] or run_result["mode"] != "incremental":
                violations.append("RunResult run_id/mode differ from the live manifest")
            terminal_status = {
                "run_published": "published", "run_no_change": "no_change",
                "run_rejected": "rejected", "run_interrupted": "interrupted",
            }.get(terminal_type)
            if terminal_status is not None and run_result["status"] != terminal_status:
                violations.append(
                    f"RunResult status {run_result['status']!r} does not map to terminal event {terminal_type!r}"
                )
            if terminal_type == "run_published":
                if run_result["exit_code"] != 0 or run_result["release_id"] is None:
                    violations.append("run_published requires RunResult published/exit 0/non-null release_id")
            elif terminal_type == "run_no_change":
                if run_result["exit_code"] != 0 or run_result["release_id"] is not None:
                    violations.append("run_no_change requires RunResult no_change/exit 0/null release_id")
            elif terminal_type == "run_rejected":
                if run_result["exit_code"] in {None, 0} or run_result["release_id"] is not None:
                    violations.append("run_rejected requires RunResult rejected/non-zero exit/null release_id")
            elif terminal_type == "run_interrupted":
                if run_result["exit_code"] is not None or run_result["release_id"] is not None:
                    violations.append("run_interrupted requires RunResult interrupted/null exit/null release_id")

    if violations:
        raise ContractViolation("live-event-stream-v1.2", violations)
    return {
        "effective_request_ids": list(effective),
        "request_stats": {
            "planned": 4, "started": started, "succeeded": succeeded,
            "failed": failed, "not_started": not_started,
        },
    }


def validate_live_event_stream(
    manifest: JsonObject,
    events: list[JsonObject],
    run_result: JsonObject | None = None,
) -> JsonObject:
    """Dispatch to one immutable live execution profile by manifest version."""
    version = manifest.get("run_schema_version") if isinstance(manifest, dict) else None
    if version == "1.1.0":
        return _validate_live_event_stream_v11(manifest, events, run_result)
    if version == "1.2.0":
        return _validate_live_event_stream_v12(manifest, events, run_result)
    if version == "1.3.0":
        return _validate_live_event_stream_v13(manifest, events, run_result)
    raise ContractViolation("live-event-stream", f"unsupported live manifest version: {version!r}")
