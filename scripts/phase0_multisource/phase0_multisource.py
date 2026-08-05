"""Collect, reconcile, and verify the Phase 0 multi-source history snapshot.

Only Python's standard library is used so the evidence can be replayed in a
clean environment.  The collector is deliberately low-rate and records a
durable start and terminal event for every HTTP request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "artifacts" / "phase-0-multisource"
CATALOG = ROOT / "source-catalog.json"
CONTRACT = REPO / "docs" / "roadmap" / "phase-0-acceptance-contract-v1.5.json"
GAMES = ("ssq", "dlt")
SOURCES = ("ydniu", "eastmoney", "gdlottery")
TARGET_COUNT = 200
USER_AGENT = "autoresearch-lotte-phase0/1.5 (internal data feasibility study)"


class Phase0Error(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def append_event(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Phase0Error(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise Phase0Error(f"invalid JSONL: {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise Phase0Error(f"expected JSON object: {path}:{line_number}")
        result.append(value)
    return result


def source_requests(catalog: dict[str, Any]) -> list[dict[str, str]]:
    requests: list[dict[str, str]] = []
    for source in catalog["sources"]:
        if not source["collection_approved_for_phase0_internal_research"]:
            continue
        for game, spec in source.get("games", {}).items():
            for page in range(1, spec["pages"] + 1):
                requests.append({
                    "source_id": source["source_id"], "game": game, "page": str(page),
                    "url": spec["url_template"].format(game=game, page=page),
                })
        for index, spec in enumerate(source.get("fixed_requests", []), 1):
            requests.append({
                "source_id": source["source_id"], "game": spec["game"], "page": str(index),
                "url": spec["url"],
            })
    return requests


def http_get(url: str, timeout: float) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = int(response.status)
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
    return status, body, headers


def collect_request(
    *, snapshot_dir: Path, spec: dict[str, str], event_path: Path, timeout: float,
    fetcher: Callable[[str, float], tuple[int, bytes, dict[str, str]]] = http_get,
) -> dict[str, Any]:
    request_id = f"{spec['source_id']}-{spec['game']}-p{int(spec['page']):03d}"
    started_at = utc_now()
    append_event(event_path, {
        "event": "request_started", "request_id": request_id, "observed_at_utc": started_at,
        "source_id": spec["source_id"], "game": spec["game"], "url": spec["url"],
    })
    started_clock = time.monotonic()
    try:
        status, body, headers = fetcher(spec["url"], timeout)
        if status != 200:
            raise Phase0Error(f"unexpected HTTP status {status}")
        if len(body) < 500:
            raise Phase0Error(f"response too small: {len(body)} bytes")
        relative = Path("raw") / spec["source_id"] / spec["game"] / f"page-{int(spec['page']):03d}.html"
        target = snapshot_dir / relative
        if target.exists():
            raise Phase0Error(f"immutable raw target already exists: {target}")
        atomic_write(target, body)
        result = {
            "request_id": request_id, "source_id": spec["source_id"], "game": spec["game"],
            "page": int(spec["page"]), "url": spec["url"], "http_status": status,
            "captured_at_utc": utc_now(), "duration_ms": round((time.monotonic() - started_clock) * 1000),
            "content_type": headers.get("content-type"), "content_length": len(body),
            "raw_ref": relative.as_posix(), "raw_sha256": sha256_bytes(body), "outcome": "success",
        }
        append_event(event_path, {"event": "request_succeeded", **result, "observed_at_utc": result["captured_at_utc"]})
        return result
    except BaseException as exc:
        failed = {
            "request_id": request_id, "source_id": spec["source_id"], "game": spec["game"],
            "page": int(spec["page"]), "url": spec["url"], "outcome": "failure",
            "failed_at_utc": utc_now(), "duration_ms": round((time.monotonic() - started_clock) * 1000),
            "error_type": type(exc).__name__, "error_message": str(exc),
        }
        append_event(event_path, {"event": "request_failed", **failed, "observed_at_utc": failed["failed_at_utc"]})
        return failed


def collect(snapshot_id: str, timeout: float, delay: float) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z_.-]+", snapshot_id):
        raise Phase0Error("snapshot-id may contain only letters, digits, dot, underscore, and dash")
    catalog = load_json(CATALOG)
    snapshot_dir = ROOT / "snapshots" / snapshot_id
    if snapshot_dir.exists():
        raise Phase0Error(f"snapshot already exists: {snapshot_dir}")
    snapshot_dir.mkdir(parents=True)
    event_path = snapshot_dir / "request-events.jsonl"
    manifest: list[dict[str, Any]] = []
    specs = source_requests(catalog)
    for index, spec in enumerate(specs):
        manifest.append(collect_request(
            snapshot_dir=snapshot_dir, spec=spec, event_path=event_path, timeout=timeout,
        ))
        if delay and index + 1 < len(specs):
            time.sleep(delay)
    successes = [item for item in manifest if item["outcome"] == "success"]
    failures = [item for item in manifest if item["outcome"] == "failure"]
    atomic_write(snapshot_dir / "capture-manifest.jsonl", b"".join(canonical_bytes(item) for item in manifest))
    summary = {
        "schema_version": "1.0.0", "snapshot_id": snapshot_id, "completed_at_utc": utc_now(),
        "planned_requests": len(specs), "successful_requests": len(successes),
        "failed_requests": len(failures), "status": "PASS" if not failures else "FAIL",
        "source_catalog_sha256": sha256_bytes(CATALOG.read_bytes()),
    }
    atomic_write(snapshot_dir / "collection-summary.json", canonical_bytes(summary))
    atomic_write(ROOT / "active-snapshot.json", canonical_bytes({"snapshot_id": snapshot_id}))
    if failures:
        raise Phase0Error(f"collection had {len(failures)} failed requests; see {event_path}")
    return snapshot_dir


def normalize_issue(game: str, issue: str) -> str:
    issue = issue.strip()
    if game == "dlt" and re.fullmatch(r"\d{5}", issue):
        issue = "20" + issue
    if not re.fullmatch(r"20\d{5}", issue):
        raise Phase0Error(f"invalid {game} issue id: {issue!r}")
    return issue


def validate_numbers(game: str, front: list[int], back: list[int]) -> None:
    expected = (6, 1, 33, 16) if game == "ssq" else (5, 2, 35, 12)
    front_count, back_count, front_max, back_max = expected
    if len(front) != front_count or len(back) != back_count:
        raise Phase0Error(f"{game}: invalid ball counts: {front}+{back}")
    if front != sorted(set(front)) or back != sorted(set(back)):
        raise Phase0Error(f"{game}: numbers must be unique and sorted: {front}+{back}")
    if not all(1 <= item <= front_max for item in front) or not all(1 <= item <= back_max for item in back):
        raise Phase0Error(f"{game}: number out of range: {front}+{back}")


def core_fact(game: str, issue_id: str, draw_date: str, front: list[int], back: list[int]) -> dict[str, Any]:
    return {
        "game": game, "issue_id": issue_id, "draw_date": draw_date,
        "front_numbers": front, "back_numbers": back,
    }


def parse_ydniu(body: bytes, game: str, provenance: dict[str, Any]) -> list[dict[str, Any]]:
    text = body.decode("utf-8")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S)
    records = []
    for row in rows:
        issue_match = re.search(r"<td>\s*(20\d{5})\s*</td>", row, flags=re.I)
        date_match = re.search(r"<td>\s*(20\d{2}-\d{2}-\d{2})[^<]*</td>", row, flags=re.I)
        if not issue_match or not date_match or "open_number" not in row:
            continue
        issue_id = normalize_issue(game, issue_match.group(1))
        if game == "ssq":
            front = [int(item) for item in re.findall(r'<i class="hq">(\d{2})</i>', row, flags=re.I)]
            back = [int(item) for item in re.findall(r'<i class="lq">(\d{2})</i>', row, flags=re.I)]
        else:
            front = [int(item) for item in re.findall(r'<i class="lq">(\d{2})</i>', row, flags=re.I)]
            back = [int(item) for item in re.findall(r'<i class="yq">(\d{2})</i>', row, flags=re.I)]
        validate_numbers(game, front, back)
        records.append(observation("ydniu", game, issue_id, date_match.group(1), front, back, provenance))
    if not records:
        raise Phase0Error(f"ydniu parser found no {game} records")
    return records


def parse_eastmoney(body: bytes, game: str, provenance: dict[str, Any]) -> list[dict[str, Any]]:
    text = body.decode("utf-8-sig")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S)
    records = []
    for row in rows:
        issue_match = re.search(rf"/Result/Category/{game}\?[^\"']*?id=(\d{{5,7}})", row, flags=re.I)
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})\(", row)
        if not issue_match or not date_match:
            continue
        front = [int(item) for item in re.findall(r'<span[^>]*class="[^"]*\bred\b[^"]*"[^>]*>(\d{2})</span>', row, flags=re.I)]
        back = [int(item) for item in re.findall(r'<span[^>]*class="[^"]*\bblue\b[^"]*"[^>]*>(\d{2})</span>', row, flags=re.I)]
        issue_id = normalize_issue(game, issue_match.group(1))
        validate_numbers(game, front, back)
        records.append(observation("eastmoney", game, issue_id, date_match.group(1), front, back, provenance))
    if not records:
        raise Phase0Error(f"eastmoney parser found no {game} records")
    return records


def parse_gdlottery(body: bytes, game: str, provenance: dict[str, Any]) -> list[dict[str, Any]]:
    if game != "dlt":
        raise Phase0Error("gdlottery parser is configured only for DLT official announcements")
    text = body.decode("utf-8")
    issue_match = re.search(r"第(\d{5})期开奖公告", text)
    date_match = re.search(r"开奖日期：(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    numbers_match = re.search(
        r"本期开奖号码：</li>\s*<li>([0-9 ]+)</li>\s*<li>([0-9 ]+)</li>", text, flags=re.I,
    )
    if not issue_match or not date_match or not numbers_match:
        raise Phase0Error("gdlottery parser could not find issue, date, and draw numbers")
    issue_id = normalize_issue(game, issue_match.group(1))
    draw_date = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
    front = [int(item) for item in numbers_match.group(1).split()]
    back = [int(item) for item in numbers_match.group(2).split()]
    validate_numbers(game, front, back)
    return [observation("gdlottery", game, issue_id, draw_date, front, back, provenance)]


def observation(
    source_id: str, game: str, issue_id: str, draw_date: str, front: list[int], back: list[int],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    fact = core_fact(game, issue_id, draw_date, front, back)
    return {
        "schema_version": "1.0.0", "source_id": source_id, **fact,
        "core_fact_sha256": sha256_bytes(canonical_bytes(fact)),
        "source_url": provenance["url"], "raw_ref": provenance["raw_ref"],
        "raw_sha256": provenance["raw_sha256"], "captured_at_utc": provenance["captured_at_utc"],
    }


PARSERS = {"ydniu": parse_ydniu, "eastmoney": parse_eastmoney, "gdlottery": parse_gdlottery}


def deduplicate(records: list[dict[str, Any]], source_id: str, game: str) -> list[dict[str, Any]]:
    by_issue: dict[str, dict[str, Any]] = {}
    for record in records:
        issue = record["issue_id"]
        previous = by_issue.get(issue)
        if previous and previous["core_fact_sha256"] != record["core_fact_sha256"]:
            raise Phase0Error(f"{source_id}/{game}: conflicting duplicate issue {issue}")
        by_issue.setdefault(issue, record)
    return sorted(by_issue.values(), key=lambda item: (item["draw_date"], item["issue_id"]), reverse=True)


def year_contiguous(records: list[dict[str, Any]]) -> tuple[bool, list[dict[str, str]]]:
    gaps: list[dict[str, str]] = []
    for newer, older in zip(records, records[1:]):
        newer_year, older_year = newer["issue_id"][:4], older["issue_id"][:4]
        if newer_year == older_year and int(newer["issue_id"][-3:]) - int(older["issue_id"][-3:]) != 1:
            gaps.append({"newer_issue": newer["issue_id"], "older_issue": older["issue_id"]})
        if newer["draw_date"] <= older["draw_date"]:
            gaps.append({"newer_issue": newer["issue_id"], "older_issue": older["issue_id"], "reason": "date_order"})
    return not gaps, gaps


def evaluate_corroboration(
    primary_record: dict[str, Any], observations: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Apply the v1.5 zero-conflict contract to one frozen issue."""
    matching = {
        source_id: record for source_id, record in observations.items()
        if record["core_fact_sha256"] == primary_record["core_fact_sha256"]
    }
    dissenting = {
        source_id: record for source_id, record in observations.items()
        if record["core_fact_sha256"] != primary_record["core_fact_sha256"]
    }
    if not observations:
        status = "missing_corroborator"
    elif dissenting:
        # A matching publication cannot cancel an observed conflict.
        status = "conflict"
    elif matching:
        status = "accepted_two_publisher_agreement"
    else:
        raise Phase0Error("corroboration classification was not exhaustive")
    return status, matching, dissenting


def build_snapshot(snapshot_dir: Path, output_root: Path | None = None) -> dict[str, Any]:
    output_root = output_root or snapshot_dir
    manifest = load_jsonl(snapshot_dir / "capture-manifest.jsonl")
    collection_summary = load_json(snapshot_dir / "collection-summary.json")
    if collection_summary["source_catalog_sha256"] != sha256_bytes(CATALOG.read_bytes()):
        raise Phase0Error("source catalog changed after collection")
    if any(item["outcome"] != "success" for item in manifest):
        raise Phase0Error("capture manifest contains failed requests")
    events = load_jsonl(snapshot_dir / "request-events.jsonl")
    started = {item["request_id"] for item in events if item["event"] == "request_started"}
    succeeded = {item["request_id"] for item in events if item["event"] == "request_succeeded"}
    failed = {item["request_id"] for item in events if item["event"] == "request_failed"}
    manifested = {item["request_id"] for item in manifest}
    if failed or started != succeeded or started != manifested or len(events) != 2 * len(manifest):
        raise Phase0Error("request event audit is incomplete or inconsistent with the capture manifest")
    records_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in manifest:
        raw_path = snapshot_dir / entry["raw_ref"]
        body = raw_path.read_bytes()
        if sha256_bytes(body) != entry["raw_sha256"]:
            raise Phase0Error(f"raw SHA-256 mismatch: {raw_path}")
        parser = PARSERS[entry["source_id"]]
        records_by_key.setdefault((entry["source_id"], entry["game"]), []).extend(parser(body, entry["game"], entry))
    for (source, game), records in records_by_key.items():
        cleaned = deduplicate(records, source, game)
        records_by_key[(source, game)] = cleaned
        atomic_write(output_root / "parsed" / f"{source}-{game}.jsonl", b"".join(canonical_bytes(item) for item in cleaned))

    report_games = []
    canonical_records: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    for game in GAMES:
        primary = records_by_key[("ydniu", game)]
        corroborators = {
            source_id: {item["issue_id"]: item for item in records_by_key.get((source_id, game), [])}
            for source_id in ("eastmoney", "gdlottery")
            if records_by_key.get((source_id, game))
        }
        published_by_any = set().union(*(set(records) for records in corroborators.values()))
        anchor_index = next((index for index, item in enumerate(primary) if item["issue_id"] in published_by_any), None)
        if anchor_index is None:
            raise Phase0Error(f"{game}: sources have no common issue")
        expected = primary[anchor_index:anchor_index + TARGET_COUNT]
        if len(expected) != TARGET_COUNT:
            raise Phase0Error(f"{game}: only {len(expected)} primary records available from common anchor")
        contiguous, gaps = year_contiguous(expected)
        accepted = 0
        missing: list[str] = []
        conflicts: list[dict[str, Any]] = []
        for primary_record in expected:
            issue = primary_record["issue_id"]
            observations = {
                source_id: records[issue] for source_id, records in corroborators.items() if issue in records
            }
            status, matching, dissenting = evaluate_corroboration(primary_record, observations)
            if status == "missing_corroborator":
                missing.append(issue)
            elif status == "conflict":
                conflicts.append({
                    "issue_id": issue, "ydniu_hash": primary_record["core_fact_sha256"],
                    "corroborator_hashes": {source: record["core_fact_sha256"] for source, record in observations.items()},
                })
            else:
                accepted += 1
                fact = core_fact(game, issue, primary_record["draw_date"], primary_record["front_numbers"], primary_record["back_numbers"])
                canonical_records.append({
                    "schema_version": "1.0.0", **fact, "status": "verified_cross_publication",
                    "core_fact_sha256": primary_record["core_fact_sha256"],
                    "evidence_refs": [primary_record["raw_ref"], *[record["raw_ref"] for record in matching.values()]],
                    "source_ids": ["ydniu", *matching.keys()],
                })
            reconciliation.append({
                "schema_version": "1.0.0", "game": game, "issue_id": issue, "status": status,
                "primary_hash": primary_record["core_fact_sha256"],
                "corroborator_hashes": {source: record["core_fact_sha256"] for source, record in observations.items()},
                "matching_source_ids": sorted(matching), "dissenting_source_ids": sorted(dissenting),
            })
        source_lag = [item["issue_id"] for item in primary[:anchor_index]]
        passed = accepted == TARGET_COUNT and not missing and not conflicts and contiguous
        report_games.append({
            "game": game, "primary_source": "ydniu", "corroborating_sources": sorted(corroborators),
            "primary_observed_unique": len(primary),
            "corroborator_observed_unique": {source: len(records) for source, records in corroborators.items()},
            "frozen_anchor_issue": expected[0]["issue_id"], "frozen_oldest_issue": expected[-1]["issue_id"],
            "frozen_expected_count": TARGET_COUNT, "accepted_count": accepted,
            "missing_count": len(missing), "missing_issue_ids": missing,
            "conflict_count": len(conflicts), "conflicts": conflicts,
            "continuous_issue_sequence": contiguous, "continuity_findings": gaps,
            "primary_newer_than_corroborator": source_lag,
            "outcome": "PASS" if passed else "FAIL",
        })

    canonical_records.sort(key=lambda item: (item["game"], item["draw_date"], item["issue_id"]), reverse=True)
    reconciliation.sort(key=lambda item: (item["game"], item["issue_id"]), reverse=True)
    atomic_write(output_root / "consensus" / "canonical-records.jsonl", b"".join(canonical_bytes(item) for item in canonical_records))
    atomic_write(output_root / "consensus" / "reconciliation.jsonl", b"".join(canonical_bytes(item) for item in reconciliation))
    passed = all(item["outcome"] == "PASS" for item in report_games)
    report = {
        "schema_version": "1.0.0", "artifact_type": "phase0_multisource_feasibility_report",
        "snapshot_id": snapshot_dir.name, "contract_version": "1.5", "target_per_game": TARGET_COUNT,
        "input_hashes": {
            "source_catalog_sha256": sha256_bytes(CATALOG.read_bytes()),
            "acceptance_contract_sha256": sha256_bytes(CONTRACT.read_bytes()),
            "pipeline_sha256": sha256_bytes(Path(__file__).read_bytes()),
        },
        "consensus_semantics": "two different publishers agree on the same official draw fact; not independent draw generation",
        "games": report_games,
        "gates": {
            "source_access": "PASS", "durable_failure_audit": "PASS",
            "parse_and_normalize": "PASS", "cross_publication_agreement": "PASS" if passed else "FAIL",
            "continuous_200_each": "PASS" if passed else "FAIL",
            "later_modeling_data_sufficiency": "PASS" if passed else "FAIL",
        },
        "project_decision": "GO" if passed else "HOLD",
        "phase0_core_goal": "ACHIEVED" if passed else "NOT_ACHIEVED",
        "limitations": [
            "The two publishers ultimately report the same official draw process; agreement detects publication or parsing errors but is not independent physical observation.",
            "The snapshot is approved only for internal Phase 0 research; source terms must be re-reviewed before redistribution or a production collection service.",
            "Historical outcomes are sufficient to begin modeling research but do not imply that lottery draws are predictable or that prediction accuracy can approach 100%.",
        ],
    }
    report_bytes = canonical_bytes(report)
    atomic_write(output_root / "phase0-report.json", report_bytes)
    atomic_write(output_root / "phase0-report.json.sha256", (sha256_bytes(report_bytes) + "\n").encode("ascii"))
    hashes = {}
    for relative in (
        "capture-manifest.jsonl", "request-events.jsonl", "collection-summary.json",
        "parsed/ydniu-ssq.jsonl", "parsed/ydniu-dlt.jsonl",
        "parsed/eastmoney-ssq.jsonl", "parsed/eastmoney-dlt.jsonl",
        "parsed/gdlottery-dlt.jsonl",
        "consensus/canonical-records.jsonl", "consensus/reconciliation.jsonl", "phase0-report.json",
    ):
        path = output_root / relative
        hashes[relative] = sha256_bytes(path.read_bytes())
    atomic_write(output_root / "artifact-hashes.json", canonical_bytes(hashes))
    return report


def verify(snapshot_dir: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase0-v15-replay-") as temporary:
        replay = Path(temporary)
        # The raw evidence and manifests remain immutable inputs; derived outputs are rebuilt.
        for name in ("capture-manifest.jsonl", "request-events.jsonl", "collection-summary.json"):
            atomic_write(replay / name, (snapshot_dir / name).read_bytes())
        build_snapshot(snapshot_dir, replay)
        compared = [
            "parsed/ydniu-ssq.jsonl", "parsed/ydniu-dlt.jsonl",
            "parsed/eastmoney-ssq.jsonl", "parsed/eastmoney-dlt.jsonl",
            "parsed/gdlottery-dlt.jsonl",
            "consensus/canonical-records.jsonl", "consensus/reconciliation.jsonl", "phase0-report.json",
        ]
        mismatches = [relative for relative in compared if (snapshot_dir / relative).read_bytes() != (replay / relative).read_bytes()]
    events = load_jsonl(snapshot_dir / "request-events.jsonl")
    started = {item["request_id"] for item in events if item["event"] == "request_started"}
    terminal = {item["request_id"] for item in events if item["event"] in {"request_succeeded", "request_failed"}}
    manifest = load_jsonl(snapshot_dir / "capture-manifest.jsonl")
    orphaned = sorted(started - terminal)
    report = load_json(snapshot_dir / "phase0-report.json")
    ok = not mismatches and not orphaned and len(started) == len(manifest) and report["phase0_core_goal"] == "ACHIEVED"
    result = {
        "status": "PASS" if ok else "FAIL", "snapshot_id": snapshot_dir.name,
        "replay_mismatches": mismatches, "orphaned_request_ids": orphaned,
        "request_count": len(started), "manifest_count": len(manifest),
        "phase0_core_goal": report["phase0_core_goal"],
    }
    atomic_write(snapshot_dir / "offline-verification.json", canonical_bytes(result))
    if not ok:
        raise Phase0Error(json.dumps(result, ensure_ascii=False))
    return result


def active_snapshot() -> Path:
    snapshot_id = load_json(ROOT / "active-snapshot.json")["snapshot_id"]
    return ROOT / "snapshots" / snapshot_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--snapshot-id", required=True)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--delay", type=float, default=0.75)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--snapshot", type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args(argv)
    if args.command == "collect":
        result = {"status": "PASS", "snapshot": str(collect(args.snapshot_id, args.timeout, args.delay))}
    elif args.command == "build":
        snapshot = args.snapshot.resolve() if args.snapshot else active_snapshot()
        result = build_snapshot(snapshot)
    else:
        snapshot = args.snapshot.resolve() if args.snapshot else active_snapshot()
        result = verify(snapshot)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Phase0Error, OSError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
