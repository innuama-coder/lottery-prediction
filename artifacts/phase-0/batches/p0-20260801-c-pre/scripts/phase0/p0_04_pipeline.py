"""P0-04 fixture/live pipeline producing raw, manifest, lock, and normalized data."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import re
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable

from p0_04_http import (
    AcquisitionError,
    ClockCheck,
    FetchResult,
    PublicHttpCollector,
    clock_check_from_json,
    clock_check_to_json,
    http_date_as_iso,
    run_windows_clock_check,
)
from p0_04_parser import ParseError, ParsedDraw, canonical_issue_id, decode_html, parse_dlt_html, parse_ssq_history_html
from phase0lib import ValidationError, canonical_json_bytes, canonical_sha256, load_json, validate_schema_instance


REPO = Path(__file__).resolve().parents[2]
PARSER_PATH = Path(__file__).with_name("p0_04_parser.py")
RULE_BUNDLES_PATH = REPO / "artifacts" / "phase-0" / "rule-bundles.json"
SOURCE_CATALOG_PATH = REPO / "artifacts" / "phase-0" / "source-catalog.json"
SCHEMA_ROOT = REPO / "artifacts" / "phase-0" / "schemas"
SENSITIVE_HEADER_NAMES = frozenset({"authorization", "cookie", "proxy-authorization", "set-cookie"})
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.I),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+", re.I),
    re.compile(r"(?:session|token|password|secret)\s*=", re.I),
)


@dataclass(frozen=True)
class ProcessOutcome:
    evidence: dict[str, Any]
    normalized: dict[str, Any] | None
    parse_result: dict[str, Any] | None
    environment_lock: dict[str, Any]


def dlt_issue_url(issue_id: str) -> str:
    canonical = canonical_issue_id("dlt", issue_id)
    return f"https://www.gdlottery.cn/f_html/kjgg/P085_{canonical[2:]}.html"


def ssq_history_url() -> str:
    return "https://www.gdfc.org.cn/sjfx/ssq_200.html"


def require_collection_approved(game: str, url: str, catalog_path: Path = SOURCE_CATALOG_PATH) -> dict[str, Any]:
    """Fail before collector construction unless the exact source family is scheduled."""

    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"source policy catalog is unavailable or invalid: {exc}") from exc
    readiness = [item for item in catalog.get("operational_readiness", []) if item.get("game") == game]
    if len(readiness) != 1 or not readiness[0].get("acquisition_ready") or readiness[0].get("policy_conclusion") != "ready":
        raise AcquisitionError(f"collection policy hold: {game} operational readiness is not ready")
    games = [item for item in catalog.get("games", []) if item.get("game") == game]
    if len(games) != 1:
        raise AcquisitionError(f"source policy catalog does not contain exactly one {game} entry")
    sources = [games[0]["authoritative_primary"], *games[0]["official_corroborators"]]
    matches = [source for source in sources if _source_url_matches(source["source_id"], url)]
    if len(matches) != 1:
        raise AcquisitionError(f"collection policy does not recognize the exact {game} host/path")
    source = matches[0]
    if source["source_id"] not in readiness[0].get("scheduled_source_ids", []):
        raise AcquisitionError(f"collection policy hold: source {source['source_id']} is not scheduled")
    if source.get("approved_use") != "scheduled_low_rate_fetch":
        raise AcquisitionError(f"collection policy hold: source {source['source_id']} is {source.get('approved_use')}")
    return source


def _source_url_matches(source_id: str, url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        return False
    host = (parsed.hostname or "").lower()
    if source_id == "dlt-gd-official-issue-pages":
        return host == "www.gdlottery.cn" and re.fullmatch(r"/f_html/kjgg/P085_[0-9]{5}\.html", parsed.path) is not None
    if source_id == "ssq-gd-official-history":
        return host == "www.gdfc.org.cn" and parsed.path == "/sjfx/ssq_200.html"
    return False


def build_environment_lock(created_at_utc: str) -> dict[str, Any]:
    parser_hash = _sha256_file(PARSER_PATH)
    locale_name = locale.getlocale()
    locale_value = ".".join(value for value in locale_name if value) or "unknown"
    return {
        "schema_version": "1.0.0",
        "artifact_type": "environment_lock",
        "contract_version": "1.3",
        "created_at_utc": created_at_utc,
        "runtime": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable_absolute_path": str(Path(sys.executable).resolve()),
        },
        "dependencies": [],
        "operating_system": platform.platform(),
        "locale": locale_value,
        "timezone": "Asia/Shanghai",
        "parser_artifact_sha256": parser_hash,
        "canonicalizer_profile": "RFC8785_no_binary_float",
    }


def process_capture(
    *,
    game: str,
    issue_id: str,
    fetch: FetchResult,
    clock_check: ClockCheck,
    output_root: Path,
    environment_lock: dict[str, Any] | None = None,
    actual_draw_at: str | None = None,
    actual_draw_at_evidence_ref: str | None = None,
    evidence_prefix: str = "p0-04",
) -> ProcessOutcome:
    if re.fullmatch(r"p0-[0-9]{2}", evidence_prefix) is None:
        raise ValueError("evidence_prefix must match p0-NN")
    canonical_issue = canonical_issue_id(game, issue_id)
    environment = environment_lock or build_environment_lock(fetch.retrieved_at)
    environment_bytes = canonical_json_bytes(environment) + b"\n"
    environment_hash = hashlib.sha256(environment_bytes).hexdigest()
    parser_hash = _sha256_file(PARSER_PATH)
    relative_raw = Path("artifacts/phase-0/raw") / f"{evidence_prefix}-{game}-{canonical_issue}.html"
    absolute_raw = output_root / "raw" / relative_raw.name
    relative_normalized = Path("artifacts/phase-0/normalized") / f"{evidence_prefix}-{game}-{canonical_issue}.json"
    absolute_normalized = output_root / "normalized" / relative_normalized.name
    relative_parsed = Path("artifacts/phase-0/parsed") / f"{evidence_prefix}-{game}-{canonical_issue}.json"
    absolute_parsed = output_root / "parsed" / relative_parsed.name
    _write_once_or_identical(absolute_raw, fetch.raw_body, "raw payload")

    normalized: dict[str, Any] | None = None
    parse_result: dict[str, Any] | None = None
    failure: Exception | None = None
    content_decoding_applied = False
    character_decoding_applied = False
    character_codec: str | None = None
    field_parsing_applied = False
    field_parsing_succeeded = False
    try:
        content_type = next(
            (item["value"] for item in fetch.response_headers if item["name"] == "Content-Type"), None
        )
        content_encoding = next(
            (item["value"] for item in fetch.response_headers if item["name"] == "Content-Encoding"), None
        )
        if content_encoding is not None and content_encoding.strip().lower() not in {"", "identity"}:
            raise ValueError(f"unsupported HTTP Content-Encoding without explicit decoder: {content_encoding}")
        html, character_codec = decode_html(fetch.raw_body, content_type)
        character_decoding_applied = True
        field_parsing_applied = True
        parsed = _parse(game, html, canonical_issue)
        field_parsing_succeeded = True
        bundle = resolve_rule_bundle(game, canonical_issue)
        parse_result = _parse_result(parsed, fetch, bundle, parser_hash)
        _write_once_or_identical(absolute_parsed, canonical_json_bytes(parse_result) + b"\n", "parse result")
        if (actual_draw_at is None) != (actual_draw_at_evidence_ref is None):
            raise ValueError("actual_draw_at and its evidence ref must be supplied together")
        if actual_draw_at is not None and actual_draw_at_evidence_ref is not None:
            _validate_draw_at(parsed, actual_draw_at)
        normalized = _normalized_record(
            parsed=parsed,
            draw_at=actual_draw_at,
            fetch=fetch,
            bundle=bundle,
            evidence_id=f"{evidence_prefix}-{game}-{canonical_issue}",
            draw_at_evidence_ref=actual_draw_at_evidence_ref,
        )
        _write_once_or_identical(absolute_normalized, canonical_json_bytes(normalized) + b"\n", "normalized record")
    except (ParseError, ValueError, KeyError) as exc:
        failure = exc

    evidence_id = f"{evidence_prefix}-{game}-{canonical_issue}"
    evidence = {
        "schema_version": "1.2.0",
        "artifact_type": "evidence_manifest_entry",
        "evidence_id": evidence_id,
        "game": game,
        "issue_id": canonical_issue,
        "request_method": "GET",
        "requested_url": fetch.requested_url,
        "redirect_chain": list(fetch.redirect_chain),
        "final_url": fetch.final_url,
        "retrieved_at": fetch.retrieved_at,
        "last_clock_check_at": clock_check.checked_at_utc,
        "clock_offset_seconds": clock_check.offset_seconds,
        "stored_payload_path": relative_raw.as_posix(),
        "stored_payload_sha256": fetch.raw_sha256,
        "content_decoding_applied": content_decoding_applied,
        "character_decoding_applied": character_decoding_applied,
        "character_codec": character_codec,
        "field_parsing_applied": field_parsing_applied,
        "field_parsing_succeeded": field_parsing_succeeded,
        "response_headers": list(fetch.response_headers),
        "redacted_header_names": list(fetch.redacted_header_names),
        "redaction_policy_version": "p0-04-response-header-allowlist-v1",
        "parser_artifact_sha256": parser_hash,
        "environment_lock_sha256": environment_hash,
        "normalized_record_ref": relative_normalized.as_posix(),
        "normalized_record_sha256": canonical_sha256(normalized) if normalized is not None else "0" * 64,
        "status": "unverified" if parse_result is not None and clock_check.passed else "invalid",
        "corroboration_tier": None,
    }
    if failure is not None:
        evidence["status"] = "invalid"
    return ProcessOutcome(evidence, normalized, parse_result, environment)


def resolve_rule_bundle(game: str, issue_id: str) -> dict[str, Any]:
    artifact = json.loads(RULE_BUNDLES_PATH.read_text(encoding="utf-8"))
    matches = [
        mapping["bundle_id"]
        for mapping in artifact["issue_mappings"]
        if mapping["game"] == game and mapping["issue_id"] == issue_id
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one rule bundle for {game} {issue_id}, found {len(matches)}")
    return next(bundle for bundle in artifact["bundles"] if bundle["bundle_id"] == matches[0])


def write_run_artifacts(output_root: Path, outcomes: Iterable[ProcessOutcome]) -> None:
    items = list(outcomes)
    if not items:
        raise ValueError("at least one outcome is required")
    environment = items[0].environment_lock
    if any(item.environment_lock != environment for item in items):
        raise ValueError("all outcomes in a run must share one environment lock")
    environment_bytes = canonical_json_bytes(environment) + b"\n"
    _write_once_or_identical(output_root / "environment-lock.json", environment_bytes, "environment lock")
    _append_manifest_idempotently(output_root / "evidence-manifest.jsonl", [item.evidence for item in items])


def load_existing_environment(output_root: Path) -> dict[str, Any] | None:
    path = output_root / "environment-lock.json"
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"existing environment lock is invalid: {exc}") from exc
    expected_bytes = canonical_json_bytes(value) + b"\n"
    if raw != expected_bytes:
        raise AcquisitionError("existing environment lock is not canonical JSON with one trailing newline")
    if value.get("schema_version") != "1.0.0" or value.get("artifact_type") != "environment_lock":
        raise AcquisitionError("existing environment lock has an incompatible schema identity")
    current_parser_hash = _sha256_file(PARSER_PATH)
    if value.get("parser_artifact_sha256") != current_parser_hash:
        raise AcquisitionError("existing environment lock parser hash differs from the current parser")
    return value


def _append_manifest_idempotently(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_by_id: dict[str, bytes] = {}
    if path.exists():
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise AcquisitionError("existing evidence manifest lacks a trailing newline")
        for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise AcquisitionError(f"existing evidence manifest line {line_number} is invalid") from exc
            evidence_id = value.get("evidence_id")
            if not isinstance(evidence_id, str) or evidence_id in existing_by_id:
                raise AcquisitionError(f"existing evidence manifest has invalid/duplicate evidence_id at line {line_number}")
            canonical_line = canonical_json_bytes(value) + b"\n"
            if line != canonical_line:
                raise AcquisitionError(f"existing evidence manifest line {line_number} is not canonical")
            existing_by_id[evidence_id] = line
    additions: list[bytes] = []
    seen_new: dict[str, bytes] = {}
    for entry in entries:
        evidence_id = entry["evidence_id"]
        line = canonical_json_bytes(entry) + b"\n"
        prior = existing_by_id.get(evidence_id) or seen_new.get(evidence_id)
        if prior is not None:
            if prior != line:
                raise AcquisitionError(f"evidence_id conflict with different content: {evidence_id}")
            continue
        seen_new[evidence_id] = line
        additions.append(line)
    if additions:
        mode = "ab" if path.exists() else "xb"
        with path.open(mode) as handle:
            for line in additions:
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def verify_captures(artifacts: Path, clock_check: ClockCheck) -> int:
    """Validate and deterministically replay every canonical manifest entry offline."""

    artifacts = artifacts.resolve()
    if not clock_check.passed:
        raise ValidationError("capture verification requires a passing clock check")
    environment_path = artifacts / "environment-lock.json"
    manifest_path = artifacts / "evidence-manifest.jsonl"
    if not environment_path.is_file() or not manifest_path.is_file():
        raise ValidationError("canonical environment-lock.json and evidence-manifest.jsonl are required")
    environment_raw = environment_path.read_bytes()
    environment = _load_canonical_json_bytes(environment_raw, "environment lock")
    environment_schema = load_json(SCHEMA_ROOT / "environment-lock.schema.json")
    evidence_schema = load_json(SCHEMA_ROOT / "evidence-manifest.schema.json")
    normalized_schema = load_json(SCHEMA_ROOT / "normalized-records.schema.json")
    validate_schema_instance(environment, environment_schema)
    current_parser_hash = _sha256_file(PARSER_PATH)
    if environment["parser_artifact_sha256"] != current_parser_hash:
        raise ValidationError("environment lock parser hash differs from current parser")
    environment_sha256 = hashlib.sha256(environment_raw).hexdigest()

    manifest_lines = manifest_path.read_bytes().splitlines(keepends=True)
    if not manifest_lines:
        raise ValidationError("evidence manifest is empty")
    evidence_ids: set[str] = set()
    verified = 0
    for line_number, line in enumerate(manifest_lines, 1):
        evidence = _load_canonical_json_bytes(line, f"evidence manifest line {line_number}")
        validate_schema_instance(evidence, evidence_schema)
        evidence_id = evidence["evidence_id"]
        if evidence_id in evidence_ids:
            raise ValidationError(f"duplicate evidence_id in manifest: {evidence_id}")
        evidence_ids.add(evidence_id)
        if evidence["environment_lock_sha256"] != environment_sha256:
            raise ValidationError(f"{evidence_id}: environment lock byte hash mismatch")
        if evidence["parser_artifact_sha256"] != current_parser_hash:
            raise ValidationError(f"{evidence_id}: parser hash differs from current parser")
        content_encoding = next((item["value"] for item in evidence["response_headers"] if item["name"] == "Content-Encoding"), None)
        non_identity_encoding = content_encoding is not None and content_encoding.strip().lower() not in {"", "identity"}
        if evidence["content_decoding_applied"]:
            raise ValidationError(f"{evidence_id}: this collector does not implement HTTP Content-Encoding decoding")
        if non_identity_encoding and (
            evidence["character_decoding_applied"]
            or evidence["field_parsing_applied"]
            or evidence["field_parsing_succeeded"]
            or evidence["status"] == "verified"
        ):
            raise ValidationError(f"{evidence_id}: non-identity Content-Encoding cannot enter character/field parsing")
        raw_path = _resolve_canonical_ref(artifacts, evidence["stored_payload_path"], "raw")
        normalized_path = _resolve_canonical_ref(artifacts, evidence["normalized_record_ref"], "normalized")
        if not raw_path.is_file():
            raise ValidationError(f"{evidence_id}: referenced raw artifact is missing")
        raw_bytes = raw_path.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != evidence["stored_payload_sha256"]:
            raise ValidationError(f"{evidence_id}: raw payload SHA-256 mismatch")
        evidence_prefix = evidence_id[:5]
        if evidence_prefix not in {"p0-04", "p0-06"}:
            raise ValidationError(f"{evidence_id}: unsupported evidence prefix")
        parsed_path = artifacts / "parsed" / f"{evidence_prefix}-{evidence['game']}-{evidence['issue_id']}.json"
        has_normalized = evidence["normalized_record_sha256"] != "0" * 64
        if has_normalized:
            if not normalized_path.is_file() or not parsed_path.is_file():
                raise ValidationError(f"{evidence_id}: referenced normalized/parser artifact is missing")
            normalized = _load_canonical_json_bytes(normalized_path.read_bytes(), f"{evidence_id} normalized record")
            validate_schema_instance(normalized, normalized_schema)
            if canonical_sha256(normalized) != evidence["normalized_record_sha256"]:
                raise ValidationError(f"{evidence_id}: normalized canonical hash mismatch")
            parsed = _load_canonical_json_bytes(parsed_path.read_bytes(), f"{evidence_id} parser trace")
        else:
            if normalized_path.exists():
                raise ValidationError(f"{evidence_id}: normalized canonical hash mismatch")
            if evidence["status"] != "invalid" or parsed_path.exists():
                raise ValidationError(f"{evidence_id}: invalid raw-only capture has contradictory derived artifacts")
            normalized = None
            parsed = None
        _scan_shareable_structure(environment, "environment lock")
        _scan_shareable_structure(evidence, evidence_id)
        if normalized is not None:
            _scan_shareable_structure(normalized, f"{evidence_id} normalized")
        if parsed is not None:
            _scan_shareable_structure(parsed, f"{evidence_id} parsed")
        fetch = FetchResult(
            requested_url=evidence["requested_url"],
            redirect_chain=tuple(evidence["redirect_chain"]),
            final_url=evidence["final_url"],
            retrieved_at=evidence["retrieved_at"],
            raw_body=raw_bytes,
            response_headers=tuple(evidence["response_headers"]),
            redacted_header_names=tuple(evidence["redacted_header_names"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            replay = process_capture(
                game=evidence["game"], issue_id=evidence["issue_id"], fetch=fetch,
                clock_check=clock_check, output_root=Path(temporary), environment_lock=environment,
                evidence_prefix=evidence_prefix,
            )
        if replay.parse_result != parsed:
            raise ValidationError(f"{evidence_id}: parser replay differs from stored trace")
        if replay.normalized != normalized:
            raise ValidationError(f"{evidence_id}: normalized replay differs from stored record")
        if replay.evidence != evidence:
            raise ValidationError(f"{evidence_id}: evidence replay differs from manifest")
        verified += 1
    return verified


def _load_canonical_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ValidationError(f"{label}: expected exactly one trailing newline")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) + b"\n" != raw:
        raise ValidationError(f"{label}: bytes are not canonical JSON plus newline")
    return value


def _resolve_canonical_ref(artifacts: Path, reference: str, expected_directory: str) -> Path:
    pure = PurePosixPath(reference)
    prefix = ("artifacts", "phase-0", expected_directory)
    if pure.is_absolute() or pure.parts[:3] != prefix or len(pure.parts) <= 3 or ".." in pure.parts:
        raise ValidationError(f"non-canonical {expected_directory} artifact path: {reference}")
    candidate = (artifacts / Path(*pure.parts[2:])).resolve()
    expected_root = (artifacts / expected_directory).resolve()
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise ValidationError(f"artifact path escapes {expected_directory}: {reference}") from exc
    return candidate


def _scan_shareable_structure(value: Any, label: str, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SENSITIVE_HEADER_NAMES:
                raise ValidationError(f"{label}{path}: forbidden header name {key}")
            if key == "name" and isinstance(child, str) and child.lower() in SENSITIVE_HEADER_NAMES:
                raise ValidationError(f"{label}{path}: forbidden header name {child}")
            if key == "redacted_header_names" and isinstance(child, list):
                forbidden = [name for name in child if isinstance(name, str) and name.lower() in SENSITIVE_HEADER_NAMES]
                if forbidden:
                    raise ValidationError(f"{label}{path}: sensitive redacted header names are not shareable")
            _scan_shareable_structure(child, label, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_shareable_structure(child, label, f"{path}[{index}]")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
        raise ValidationError(f"{label}{path}: sensitive header-like value is not shareable")


def _normalized_record(
    *, parsed: ParsedDraw,
    draw_at: str | None,
    fetch: FetchResult,
    bundle: dict[str, Any],
    evidence_id: str,
    draw_at_evidence_ref: str | None,
) -> dict[str, Any]:
    evidence_refs = [evidence_id]
    if draw_at_evidence_ref is not None:
        evidence_refs.append(draw_at_evidence_ref)
    return {
        "schema_version": "1.1.0",
        "artifact_type": "normalized_record",
        "record_id": f"{parsed.game}-{parsed.issue_id}-p0-04",
        "game": parsed.game,
        "issue_id": parsed.issue_id,
        "front_numbers": list(parsed.front_numbers),
        "back_numbers": list(parsed.back_numbers),
        "draw_date_local": parsed.draw_date.isoformat(),
        "draw_at": draw_at,
        "page_published_at": None,
        "http_date": http_date_as_iso(fetch.response_headers),
        "first_seen_at": fetch.retrieved_at,
        "retrieved_at": fetch.retrieved_at,
        "corrected_at": None,
        "available_at": None,
        "number_space_version": bundle["number_space_version"],
        "draw_process_version": bundle["draw_process_version"],
        "prize_rule_version": bundle["prize_rule_version"],
        "active_promotion_ids": bundle["active_promotion_ids"],
        "status": "unverified",
        "corroboration_tier": None,
        "evidence_refs": evidence_refs,
        "supersedes": None,
    }


def _parse_result(
    parsed: ParsedDraw,
    fetch: FetchResult,
    bundle: dict[str, Any],
    parser_hash: str,
) -> dict[str, Any]:
    """Intermediate parser trace; unknown actual draw time no longer blocks normalization."""

    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase0_parse_result",
        "game": parsed.game,
        "issue_id": parsed.issue_id,
        "front_numbers": list(parsed.front_numbers),
        "back_numbers": list(parsed.back_numbers),
        "draw_date_local": parsed.draw_date.isoformat(),
        "source_url": fetch.final_url,
        "stored_payload_sha256": fetch.raw_sha256,
        "parser_artifact_sha256": parser_hash,
        "number_space_version": bundle["number_space_version"],
        "draw_process_version": bundle["draw_process_version"],
        "prize_rule_version": bundle["prize_rule_version"],
        "active_promotion_ids": bundle["active_promotion_ids"],
        "status": "parsed_unverified",
        "normalization_blockers": [],
    }


def _parse(game: str, html: str, issue_id: str) -> ParsedDraw:
    if game == "dlt":
        return parse_dlt_html(html, issue_id)
    if game == "ssq":
        return parse_ssq_history_html(html, issue_id)
    raise ValueError(f"unsupported game: {game}")


def _validate_draw_at(parsed: ParsedDraw, draw_at: str) -> None:
    value = datetime.fromisoformat(draw_at.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("draw_at must include an explicit timezone")
    shanghai_date = value.astimezone(timezone(timedelta(hours=8))).date()
    if shanghai_date != parsed.draw_date:
        raise ValueError(f"draw_at date {shanghai_date} differs from source date {parsed.draw_date}")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once_or_identical(path: Path, value: bytes, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != value:
            raise AcquisitionError(f"{label} conflict; refusing to overwrite: {path}")
        return
    try:
        with path.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != value:
            raise AcquisitionError(f"{label} conflict; refusing to overwrite: {path}")


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(value)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _load_clock(path: Path) -> ClockCheck:
    return clock_check_from_json(json.loads(path.read_text(encoding="utf-8")))


def _command_clock_check(args: argparse.Namespace) -> int:
    check = run_windows_clock_check(args.maximum_offset_seconds)
    Path(args.output).write_text(json.dumps(clock_check_to_json(check), indent=2) + "\n", encoding="utf-8")
    return 0 if check.passed else 2


def _command_collect(args: argparse.Namespace) -> int:
    clock = _load_clock(Path(args.clock_check))
    game = args.game
    issue_id = canonical_issue_id(game, args.issue)
    url = dlt_issue_url(issue_id) if game == "dlt" else ssq_history_url()
    require_collection_approved(game, url)
    output_root = Path(args.output_root)
    environment = load_existing_environment(output_root)
    collector = PublicHttpCollector(minimum_interval_seconds=30, timeout_seconds=args.timeout)
    fetch = collector.fetch(url, clock_check=clock)
    outcome = process_capture(
        game=game,
        issue_id=issue_id,
        fetch=fetch,
        clock_check=clock,
        output_root=output_root,
        environment_lock=environment,
    )
    write_run_artifacts(output_root, [outcome])
    return 0 if outcome.parse_result is not None else 3


def _command_verify_captures(args: argparse.Namespace) -> int:
    clock = _load_clock(Path(args.clock_check))
    count = verify_captures(Path(args.artifacts), clock)
    print(json.dumps({"status": "PASS", "command": "verify-captures", "verified_entries": count, "network_used": False}, separators=(",", ":")))
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    clock = subparsers.add_parser("clock-check", help="record the mandatory Windows clock check")
    clock.add_argument("--output", required=True)
    clock.add_argument("--maximum-offset-seconds", type=int, default=5)
    clock.set_defaults(handler=_command_clock_check)
    collect = subparsers.add_parser("collect", help="perform one ordinary public HTTPS GET")
    collect.add_argument("--game", choices=("dlt", "ssq"), required=True)
    collect.add_argument("--issue", required=True)
    collect.add_argument("--clock-check", required=True)
    collect.add_argument("--output-root", default="artifacts/phase-0")
    collect.add_argument("--timeout", type=float, default=20.0)
    collect.set_defaults(handler=_command_collect)
    verify = subparsers.add_parser("verify-captures", help="offline schema/hash/replay verification of canonical captures")
    verify.add_argument("--artifacts", default="artifacts/phase-0")
    verify.add_argument("--clock-check", required=True)
    verify.set_defaults(handler=_command_verify_captures)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except (AcquisitionError, ParseError, ValueError, KeyError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "command": arguments.command, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
