from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from lottery_data.parsers.gdlottery_history import parse as parse_gdlottery
from lottery_data.parsers.swlc import parse as parse_swlc
from lottery_data.parsers.ydniu import parse as parse_ydniu

from .identity import content_id
from .serialization import canonical_json_bytes, load_json, sha256_bytes, sha256_file
from .storage import resolve_inside, write_once_bytes, write_once_json
from .verification import SOURCE_PAIRS, deduplicate_facts, normalized_fact, verify_result_revision


EXPECTED_ENDPOINTS = {
    ("ssq", "swlc"): "https://www.swlc.net.cn/shsflcpfxzx/lottery/ssq.html?view=previous&limit=30",
    ("ssq", "ydniu"): "https://www.ydniu.com/open/ssq-500/1.html",
    ("dlt", "gdlottery"): "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
    ("dlt", "ydniu"): "https://www.ydniu.com/open/dlt-500/1.html",
}
FROZEN_SOURCE_POLICY_SHA256 = "ab70c9abc440ad8180db70321578e4a569fbfda802b0e698597fd5ae3df7417f"
FROZEN_RATE_LIMIT = (
    "phase4-source-rate-v1:max_requests_per_endpoint_per_canary=1;"
    "minimum_same_host_interval_seconds=2;request_timeout_seconds=30;"
    "max_attempts=2;retry_backoff_seconds=2"
)
EXPECTED_CONTENT_TYPES = {
    ("ssq", "swlc"): "text/html",
    ("ssq", "ydniu"): "text/html",
    ("dlt", "gdlottery"): "application/json",
    ("dlt", "ydniu"): "text/html",
}
PROTECTED_ROOTS = (
    "artifacts/phase-0",
    "artifacts/phase-0-multisource",
    "artifacts/phase-1",
    "artifacts/phase-2",
    "artifacts/phase-2.1",
    "artifacts/phase-3",
)


class SourcePolicyError(ValueError):
    exit_code = 20
    terminal = "HOLD_SOURCE_POLICY"


class SourceReadinessError(RuntimeError):
    exit_code = 20
    terminal = "HOLD_SOURCE_READINESS"


class RetryableSourceError(RuntimeError):
    exit_code = 30
    terminal = "retryable_network_failure"


class ProtectedRootMutation(RuntimeError):
    exit_code = 6
    terminal = "FAIL_PROTECTED_ROOT_WRITE"


@dataclass(frozen=True)
class SourceEndpoint:
    game: str
    source_id: str
    publisher: str
    role: str
    endpoint: str
    maximum_response_bytes: int
    expected_content_type: str

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.endpoint).hostname or ""


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourcePolicyError("source policy timestamp is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise SourcePolicyError("source policy timestamp lacks an offset")
    return parsed.astimezone(timezone.utc)


def _validate_url(value: str, expected: str) -> None:
    if value != expected:
        raise SourcePolicyError("source endpoint differs from the reviewed exact endpoint")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or parsed.port is not None or parsed.fragment:
        raise SourcePolicyError("source endpoint violates the HTTPS authority boundary")


def load_source_policy(path: Path, *, at_utc: str) -> tuple[dict[str, Any], list[SourceEndpoint]]:
    if sha256_file(path) != FROZEN_SOURCE_POLICY_SHA256:
        raise SourcePolicyError("source policy bytes differ from the frozen custodian review")
    policy = load_json(path, reject_floats=True)
    required = {
        "schema_version", "artifact_type", "policy_id", "purpose", "reviewed_at_utc",
        "expires_at_utc", "sources", "scheduled_internal_mvp_collection_approved",
        "reviewer_provenance",
    }
    if set(policy) != required or policy["schema_version"] != "1.0.0" or policy["artifact_type"] != "phase4_source_review":
        raise SourcePolicyError("Phase 4 source policy shape mismatch")
    if policy["purpose"] != "phase4_scheduled_internal_mvp_readonly_collection" or policy["scheduled_internal_mvp_collection_approved"] is not True:
        raise SourcePolicyError("source policy is not approved for the Phase 4 scheduled read-only purpose")
    observed = _utc(at_utc)
    if observed < _utc(policy["reviewed_at_utc"]) or observed > _utc(policy["expires_at_utc"]):
        raise SourcePolicyError("source policy is not valid at the invocation clock")
    rows: list[SourceEndpoint] = []
    seen: set[tuple[str, str]] = set()
    publishers_by_game: dict[str, set[str]] = {"ssq": set(), "dlt": set()}
    for supplied in policy["sources"]:
        expected_fields = {
            "game", "source_id", "publisher", "role", "endpoint", "method",
            "rate_limit", "redirect_policy", "maximum_response_bytes",
        }
        if set(supplied) != expected_fields:
            raise SourcePolicyError("source policy endpoint fields mismatch")
        key = (supplied["game"], supplied["source_id"])
        if key not in EXPECTED_ENDPOINTS or key in seen:
            raise SourcePolicyError("source policy contains a missing, duplicate, or unregistered source/game pair")
        expected_role = "primary" if supplied["source_id"] == SOURCE_PAIRS[supplied["game"]][0] else "corroborating"
        if (
            supplied["role"] != expected_role
            or supplied["method"] != "GET"
            or supplied["redirect_policy"] != "same_source_only"
            or supplied["rate_limit"] != FROZEN_RATE_LIMIT
        ):
            raise SourcePolicyError("source role, method, or redirect policy mismatch")
        if type(supplied["maximum_response_bytes"]) is not int or supplied["maximum_response_bytes"] <= 0:
            raise SourcePolicyError("source response-size cap is invalid")
        _validate_url(supplied["endpoint"], EXPECTED_ENDPOINTS[key])
        seen.add(key)
        publishers_by_game[supplied["game"]].add(supplied["publisher"])
        rows.append(SourceEndpoint(
            game=supplied["game"], source_id=supplied["source_id"], publisher=supplied["publisher"],
            role=supplied["role"], endpoint=supplied["endpoint"],
            maximum_response_bytes=supplied["maximum_response_bytes"],
            expected_content_type=EXPECTED_CONTENT_TYPES[key],
        ))
    if seen != set(EXPECTED_ENDPOINTS) or any(len(publishers) != 2 for publishers in publishers_by_game.values()):
        raise SourcePolicyError("both games require distinct reviewed primary and corroborating publishers")
    return policy, sorted(rows, key=lambda row: (row.game, 0 if row.role == "primary" else 1))


class _SameSourceRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def __init__(self, allowed_host: str) -> None:
        self.allowed_host = allowed_host

    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host or parsed.port is not None or parsed.username is not None or parsed.fragment:
            raise SourcePolicyError("cross-source or unsafe redirect denied")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _default_open(endpoint: SourceEndpoint) -> Any:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SameSourceRedirect(endpoint.host))
    request = urllib.request.Request(
        endpoint.endpoint,
        method="GET",
        headers={"Accept": "application/json,text/html;q=0.9", "User-Agent": "lottery-phase4-readonly/1.0"},
    )
    return opener.open(request, timeout=30)


def capture_endpoint(
    endpoint: SourceEndpoint,
    *,
    observed_at_utc: str,
    open_response: Callable[[SourceEndpoint], Any] = _default_open,
) -> tuple[dict[str, Any], bytes]:
    try:
        with open_response(endpoint) as response:
            final_url = response.geturl()
            final = urllib.parse.urlsplit(final_url)
            if final.scheme != "https" or final.hostname != endpoint.host or final.port is not None:
                raise SourcePolicyError("response escaped the reviewed source authority")
            status = int(response.getcode())
            content_type = response.headers.get_content_type()
            if content_type != endpoint.expected_content_type:
                raise SourceReadinessError(
                    f"source content type mismatch: expected {endpoint.expected_content_type}, got {content_type}"
                )
            raw = response.read(endpoint.maximum_response_bytes + 1)
            if len(raw) > endpoint.maximum_response_bytes:
                raise SourceReadinessError("source response exceeded the reviewed size cap")
            if status != 200:
                raise SourceReadinessError(f"source returned HTTP {status}")
            terminal = "observed"
    except SourcePolicyError:
        raise
    except SourceReadinessError:
        raise
    except urllib.error.HTTPError as exc:
        raise SourceReadinessError(
            f"source returned HTTP {exc.code}: {endpoint.source_id}/{endpoint.game}"
        ) from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise RetryableSourceError(f"source request failed: {endpoint.source_id}/{endpoint.game}: {exc}") from exc
    body = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_source_observation",
        "game": endpoint.game,
        "source_id": endpoint.source_id,
        "request_url": endpoint.endpoint,
        "method": "GET",
        "observed_at_utc": observed_at_utc,
        "http_status": status,
        "raw_sha256": sha256_bytes(raw),
        "raw_bytes": len(raw),
        "terminal": terminal,
    }
    body["observation_id"] = content_id("observation", body)
    return body, raw


def protected_inventory(project_root: Path) -> list[dict[str, Any]]:
    project = project_root.resolve()
    if not all((project / relative).exists() for relative in PROTECTED_ROOTS):
        candidates = list((project / "inputs/preparation-evidence/work-items/T00").glob("protected-artifact-inventory.json"))
        if len(candidates) != 1:
            raise SourcePolicyError("protected roots and frozen T00 protected inventory are both unavailable")
        frozen = load_json(candidates[0], reject_floats=True)
        if frozen.get("artifact_type") != "phase4_protected_artifact_inventory":
            raise SourcePolicyError("frozen T00 protected inventory identity is invalid")
        return list(frozen["entries"])
    rows: list[dict[str, Any]] = []
    for relative_root in PROTECTED_ROOTS:
        root = resolve_inside(project, relative_root)
        if not root.exists():
            raise SourcePolicyError(f"protected root is missing: {relative_root}")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(project).as_posix()
            if path.is_symlink():
                rows.append({"path": relative, "type": "symlink", "target": path.readlink().as_posix()})
            elif path.is_file():
                rows.append({"path": relative, "type": "file", "bytes": path.stat().st_size, "sha256": sha256_file(path)})
            elif path.is_dir():
                rows.append({"path": relative, "type": "directory"})
            else:
                raise SourcePolicyError(f"unsupported object in protected root: {relative}")
    return rows


def parse_source_response(observation: Mapping[str, Any], raw: bytes) -> list[dict[str, Any]]:
    if len(raw) != observation["raw_bytes"] or sha256_bytes(raw) != observation["raw_sha256"]:
        raise SourceReadinessError("raw response does not match its transport observation")
    game, source_id = observation["game"], observation["source_id"]
    parser = {"swlc": parse_swlc, "gdlottery": parse_gdlottery, "ydniu": parse_ydniu}.get(source_id)
    if parser is None:
        raise SourcePolicyError("source parser is not registered")
    try:
        parsed = parser(raw, game)
        facts = [
            normalized_fact(
                source_id=source_id, game=game, observation_id=observation["observation_id"],
                issue_id=row["issue_id"], draw_business_date=row["draw_date_local"],
                front_numbers=row["front_numbers"], back_numbers=row["back_numbers"],
            )
            for row in parsed
        ]
        return deduplicate_facts(facts)
    except (UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise SourceReadinessError(f"source parser drift or invalid draw fact: {source_id}/{game}: {exc}") from exc


def run_readonly_canary(
    *,
    project_root: Path,
    source_policy_path: Path,
    staging_root: Path,
    output_root: Path,
    mode: str,
    observed_at_utc: str,
    producer_provenance: Mapping[str, Any],
    open_response: Callable[[SourceEndpoint], Any] = _default_open,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if mode not in {"early-readonly-canary", "readonly-canary"}:
        raise SourcePolicyError("source ingest mode is not a registered read-only canary")
    project = project_root.resolve()
    staging = staging_root.resolve(strict=False)
    output = output_root.resolve(strict=False)
    staging.relative_to((project / "artifacts/phase-4-staging").resolve())
    allowed_outputs = ((project / "artifacts/phase-4-prep").resolve(), (project / "artifacts/phase-4").resolve())
    if not any(output == base or base in output.parents for base in allowed_outputs):
        raise SourcePolicyError("canary output is outside Phase 4 preparation/formal evidence roots")
    policy, endpoints = load_source_policy(source_policy_path, at_utc=observed_at_utc)
    before = protected_inventory(project)
    write_once_json(resolve_inside(output, "protected-inventory-before.json"), before)
    facts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    last_host_request: dict[str, float] = {}
    try:
        for endpoint in endpoints:
            elapsed = time.monotonic() - last_host_request.get(endpoint.host, float("-inf"))
            if elapsed < 2.0:
                pause(2.0 - elapsed)
            for attempt in (1, 2):
                last_host_request[endpoint.host] = time.monotonic()
                try:
                    observation, raw = capture_endpoint(
                        endpoint, observed_at_utc=observed_at_utc, open_response=open_response,
                    )
                    break
                except RetryableSourceError as exc:
                    attempt_receipt = {
                        "schema_version": "1.0.0", "artifact_type": "phase4_source_transport_attempt",
                        "game": endpoint.game, "source_id": endpoint.source_id,
                        "request_url": endpoint.endpoint, "request_identity": f"{endpoint.game}:{endpoint.source_id}",
                        "attempt": attempt, "terminal": exc.terminal, "error": str(exc),
                    }
                    write_once_json(
                        resolve_inside(staging, f"receipts/{endpoint.source_id}-{endpoint.game}-attempt-{attempt}.json"),
                        attempt_receipt,
                    )
                    if attempt == 2:
                        raise SourceReadinessError(
                            f"source exhausted its reviewed transport attempts: {endpoint.source_id}/{endpoint.game}"
                        ) from exc
                    pause(2.0)
            raw_path = resolve_inside(staging, f"raw/{endpoint.source_id}/{endpoint.game}/{observation['raw_sha256']}.raw")
            write_once_bytes(raw_path, raw)
            write_once_json(resolve_inside(output, f"observations/{observation['observation_id']}.json"), observation)
            parsed = parse_source_response(observation, raw)
            for fact in parsed:
                write_once_json(resolve_inside(output, f"parsed/{fact['parsed_fact_id']}.json"), fact)
            facts.extend(parsed)
            observations.append(observation)
    finally:
        after = protected_inventory(project)
        write_once_json(resolve_inside(output, "protected-inventory-after.json"), after)
        if after != before:
            raise ProtectedRootMutation("Phase 0-3 protected roots changed during the source canary")
    revisions: list[dict[str, Any]] = []
    facts = deduplicate_facts(facts)
    for game in ("ssq", "dlt"):
        primary_id, corroborating_id = SOURCE_PAIRS[game]
        primary = {(row["issue_id"]): row for row in facts if row["game"] == game and row["source_id"] == primary_id}
        corroborating = {(row["issue_id"]): row for row in facts if row["game"] == game and row["source_id"] == corroborating_id}
        overlap = sorted(set(primary) & set(corroborating))
        if not overlap:
            raise SourceReadinessError(f"no overlapping published issue for {game}")
        for issue in overlap:
            revision = verify_result_revision(primary[issue], corroborating[issue], verified_at_utc=observed_at_utc)
            revisions.append(revision)
            write_once_json(resolve_inside(output, f"result-revisions/{revision['result_revision_id']}.json"), revision)
    phase1_schema_path = project / "contracts/phase1-draw-record.schema.json"
    if not phase1_schema_path.is_file():
        phase1_schema_path = project / "schemas/phase1/draw-record.schema.json"
    if not phase1_schema_path.is_file():
        phase1_schema_path = Path(__file__).resolve().parents[3] / "schemas/phase1/draw-record.schema.json"
    if not phase1_schema_path.is_file():
        raise SourceReadinessError("Phase 1 draw-record compatibility schema is unavailable")
    from jsonschema import Draft202012Validator
    schema = load_json(phase1_schema_path, reject_floats=True)
    observation_by_id = {row["observation_id"]: row for row in observations}
    endpoint_by_key = {(row.game, row.source_id): row for row in endpoints}
    compatible_records = []
    for revision in revisions:
        links = []
        for observation_id in (revision["primary_observation_id"], revision["corroborating_observation_id"]):
            observation = observation_by_id[observation_id]
            endpoint = endpoint_by_key[(revision["game"], observation["source_id"])]
            links.append({"source_id":observation["source_id"],"publisher_id":"publisher-" + endpoint.source_id,"observation_id":"obs-v1:" + observation_id.rsplit(":",1)[-1],"raw_ref":f"raw/{observation['source_id']}/{revision['game']}/{observation['raw_sha256']}.raw","raw_sha256":observation["raw_sha256"]})
        core = {"game":revision["game"],"issue_id":revision["issue_id"],"draw_date_local":revision["draw_business_date"],"front_numbers":revision["numbers"]["front"],"back_numbers":revision["numbers"]["back"]}
        record = {"record_schema_version":"1.0.0",**core,"status":"verified","core_fact_profile":"phase0-core-fact-v1","core_fact_sha256":sha256_bytes(canonical_json_bytes(core)),"evidence_links":links,"revision_id":"rev-v1:" + sha256_bytes(canonical_json_bytes(revision)),"supersedes_revision_id":None,"knowledge_class":"prospective_as_observed","available_at_utc":observed_at_utc}
        Draft202012Validator(schema).validate(record)
        compatible_records.append(record)
        write_once_json(resolve_inside(output, f"phase1-compatible/{record['revision_id']}.json"), record)
    summary = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_source_canary_summary",
        "mode": mode,
        "policy_id": policy["policy_id"],
        "source_policy_sha256": sha256_file(source_policy_path),
        "required_endpoint_count": 4,
        "successful_endpoint_count": len(observations),
        "verified_game_count": len({row["game"] for row in revisions}),
        "result_revision_ids": sorted(row["result_revision_id"] for row in revisions),
        "deduplicated_fact_count": len(facts),
        "phase1_compatible_record_count": len(compatible_records),
        "phase1_schema_compatibility": "PASS",
        "protected_roots_unchanged": after == before,
        "network_terminal_coverage": "PASS",
        "producer_provenance": dict(producer_provenance),
        "status": "PASS",
    }
    write_once_json(resolve_inside(output, "canary-summary.json"), summary)
    return summary
