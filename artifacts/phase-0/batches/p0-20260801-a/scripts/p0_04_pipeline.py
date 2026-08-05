"""P0-04 fixture/live pipeline producing raw, manifest, lock, and normalized data."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from p0_04_http import (
    ClockCheck,
    FetchResult,
    PublicHttpCollector,
    clock_check_from_json,
    clock_check_to_json,
    http_date_as_iso,
    run_windows_clock_check,
)
from p0_04_parser import ParseError, ParsedDraw, canonical_issue_id, decode_html, parse_dlt_html, parse_ssq_history_html
from phase0lib import canonical_json_bytes, canonical_sha256


REPO = Path(__file__).resolve().parents[2]
PARSER_PATH = Path(__file__).with_name("p0_04_parser.py")
RULE_BUNDLES_PATH = REPO / "artifacts" / "phase-0" / "rule-bundles.json"


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
) -> ProcessOutcome:
    canonical_issue = canonical_issue_id(game, issue_id)
    environment = environment_lock or build_environment_lock(fetch.retrieved_at)
    environment_hash = canonical_sha256(environment)
    parser_hash = _sha256_file(PARSER_PATH)
    relative_raw = Path("artifacts/phase-0/raw") / f"p0-04-{game}-{canonical_issue}.html"
    absolute_raw = output_root / "raw" / relative_raw.name
    relative_normalized = Path("artifacts/phase-0/normalized") / f"p0-04-{game}-{canonical_issue}.json"
    absolute_normalized = output_root / "normalized" / relative_normalized.name
    relative_parsed = Path("artifacts/phase-0/parsed") / f"p0-04-{game}-{canonical_issue}.json"
    absolute_parsed = output_root / "parsed" / relative_parsed.name
    _atomic_write_bytes(absolute_raw, fetch.raw_body)

    normalized: dict[str, Any] | None = None
    parse_result: dict[str, Any] | None = None
    failure: Exception | None = None
    decoded = False
    try:
        content_type = next(
            (item["value"] for item in fetch.response_headers if item["name"] == "Content-Type"), None
        )
        html, _codec = decode_html(fetch.raw_body, content_type)
        decoded = True
        parsed = _parse(game, html, canonical_issue)
        bundle = resolve_rule_bundle(game, canonical_issue)
        parse_result = _parse_result(parsed, fetch, bundle, parser_hash)
        _atomic_write_bytes(absolute_parsed, canonical_json_bytes(parse_result) + b"\n")
        if (actual_draw_at is None) != (actual_draw_at_evidence_ref is None):
            raise ValueError("actual_draw_at and its evidence ref must be supplied together")
        if actual_draw_at is not None and actual_draw_at_evidence_ref is not None:
            _validate_draw_at(parsed, actual_draw_at)
            normalized = _normalized_record(
                parsed=parsed,
                draw_at=actual_draw_at,
                fetch=fetch,
                bundle=bundle,
                evidence_id=f"p0-04-{game}-{canonical_issue}",
                draw_at_evidence_ref=actual_draw_at_evidence_ref,
            )
            _atomic_write_bytes(absolute_normalized, canonical_json_bytes(normalized) + b"\n")
        elif absolute_normalized.exists():
            absolute_normalized.unlink()
    except (ParseError, ValueError, KeyError) as exc:
        failure = exc
        if absolute_normalized.exists():
            absolute_normalized.unlink()
        if absolute_parsed.exists():
            absolute_parsed.unlink()

    evidence_id = f"p0-04-{game}-{canonical_issue}"
    evidence = {
        "schema_version": "1.0.0",
        "artifact_type": "evidence_manifest_entry",
        "evidence_id": evidence_id,
        "game": game,
        "issue_id": canonical_issue,
        "requested_url": fetch.requested_url,
        "redirect_chain": list(fetch.redirect_chain),
        "final_url": fetch.final_url,
        "retrieved_at": fetch.retrieved_at,
        "last_clock_check_at": clock_check.checked_at_utc,
        "clock_offset_seconds": clock_check.offset_seconds,
        "stored_payload_path": relative_raw.as_posix(),
        "stored_payload_sha256": fetch.raw_sha256,
        "content_decoding_applied": decoded,
        "response_headers": list(fetch.response_headers),
        "redacted_header_names": list(fetch.redacted_header_names),
        "redaction_policy_version": "p0-04-response-header-allowlist-v1",
        "parser_artifact_sha256": parser_hash,
        "environment_lock_sha256": environment_hash,
        "normalized_record_ref": relative_normalized.as_posix(),
        "normalized_record_sha256": canonical_sha256(normalized) if normalized is not None else "0" * 64,
        "status": "unverified" if parse_result is not None and clock_check.passed else "invalid",
        "corroboration_tier": "shared_upstream",
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
    _atomic_write_bytes(output_root / "p0-04-environment-lock.json", canonical_json_bytes(environment) + b"\n")
    manifest = b"".join(canonical_json_bytes(item.evidence) + b"\n" for item in items)
    _atomic_write_bytes(output_root / "p0-04-evidence-manifest.jsonl", manifest)
    normalized = b"".join(
        canonical_json_bytes(item.normalized) + b"\n" for item in items if item.normalized is not None
    )
    _atomic_write_bytes(output_root / "normalized" / "p0-04-normalized-records.jsonl", normalized)
    parsed = b"".join(
        canonical_json_bytes(item.parse_result) + b"\n" for item in items if item.parse_result is not None
    )
    _atomic_write_bytes(output_root / "parsed" / "p0-04-parse-results.jsonl", parsed)


def _normalized_record(
    *, parsed: ParsedDraw,
    draw_at: str,
    fetch: FetchResult,
    bundle: dict[str, Any],
    evidence_id: str,
    draw_at_evidence_ref: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "normalized_record",
        "record_id": f"{parsed.game}-{parsed.issue_id}-p0-04",
        "game": parsed.game,
        "issue_id": parsed.issue_id,
        "front_numbers": list(parsed.front_numbers),
        "back_numbers": list(parsed.back_numbers),
        "draw_at": draw_at,
        "page_published_at": None,
        "http_date": http_date_as_iso(fetch.response_headers),
        "first_seen_at": fetch.retrieved_at,
        "retrieved_at": fetch.retrieved_at,
        "corrected_at": None,
        "available_at": fetch.retrieved_at,
        "number_space_version": bundle["number_space_version"],
        "draw_process_version": bundle["draw_process_version"],
        "prize_rule_version": bundle["prize_rule_version"],
        "active_promotion_ids": bundle["active_promotion_ids"],
        "status": "unverified",
        "corroboration_tier": "shared_upstream",
        "evidence_refs": [evidence_id, draw_at_evidence_ref],
        "supersedes": None,
    }


def _parse_result(
    parsed: ParsedDraw,
    fetch: FetchResult,
    bundle: dict[str, Any],
    parser_hash: str,
) -> dict[str, Any]:
    """Intermediate record used while the frozen draw_at contract is unresolved."""

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
        "normalization_blockers": ["actual_draw_at_not_evidenced"],
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
    collector = PublicHttpCollector(minimum_interval_seconds=30, timeout_seconds=args.timeout)
    fetch = collector.fetch(url, clock_check=clock)
    outcome = process_capture(
        game=game,
        issue_id=issue_id,
        fetch=fetch,
        clock_check=clock,
        output_root=Path(args.output_root),
    )
    write_run_artifacts(Path(args.output_root), [outcome])
    return 0 if outcome.parse_result is not None else 3


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
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
