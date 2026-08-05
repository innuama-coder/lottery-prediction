from __future__ import annotations

import sys
import os
import json
import shutil
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import RunLayout, atomic_write_bytes, atomic_write_json, atomic_write_jsonl, load_json, write_once_json
from .models import ContractViolation, validate_live_event_stream, validate_object
from .serialization import canonical_jsonl_bytes, sha256_bytes, sha256_file
from .steps.events import EventLog
from .steps.incremental import DeltaOutsideG2Scope
from .steps.preflight import (
    BootstrapArguments, BootstrapPreflight, IncrementalArguments, IncrementalPreflight,
    PreflightError, prepare_bootstrap, prepare_incremental, validate_bootstrap_arguments,
    validate_incremental_arguments, validate_live_preflight_policy,
)
from .steps.locking import LockUnavailable, RunLock
from .steps.recovery import RecoveryConflict, recover_stale_publications, recover_stale_runs
from .steps.publish import (
    PublicationToken, PublishDestinationExistsError, PublishError, no_change_guard,
    rollback_publication, publish_release,
)
from .steps.report import RunCounters, build_run_result
from .steps.verify import RawHashMismatchError, VerifyContractError
from .steps.replay import ReplayContractError, compare_deterministic_outputs, replay_session
from .steps.live_policy import LivePolicyError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class WorkflowDependencies:
    build_request_plan: Callable[[Path, Sequence[str], Mapping[str, Any]], list[dict[str, Any]]]
    load_source_catalog: Callable[[Path], dict[str, Any]]
    audit_snapshot: Callable[[Path], dict[str, Any]]
    materialize_request: Callable[[dict[str, Any], Path, Path], dict[str, Any]]
    parse_raw: Callable[..., list[dict[str, Any]]]
    deduplicate_observations: Callable[[Sequence[Mapping[str, Any]]], list[dict[str, Any]]]
    validate_observations: Callable[[Sequence[Mapping[str, Any]]], None]
    reconcile: Callable[..., tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    build_draw_records: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], list[dict[str, Any]]]
    build_quality_report: Callable[..., dict[str, Any]]
    expected_reparsed_counts: Mapping[str, int]
    transform_observations: Callable[..., Any] | None = None
    compare_no_change: Callable[..., dict[str, Any]] | None = None
    verify_release: Callable[..., dict[str, Any]] | None = None
    clock: Callable[[], str] = utc_now


def default_dependencies() -> WorkflowDependencies:
    from .steps.normalize import build_draw_records
    from .steps import EXPECTED_REPARSED_COUNTS
    from .steps.parse import deduplicate_observations, parse_snapshot_raw
    from .steps.quality_gate import build_bootstrap_quality_report
    from .steps.reconcile import reconcile_bootstrap
    from .steps.snapshot import audit_snapshot, build_bootstrap_request_plan, load_source_catalog, materialize_snapshot_request
    from .steps.validate import validate_observations
    from .steps.transform import transform_observations
    from .steps.incremental import compare_no_change
    try:
        from .steps.verify import verify_release
    except ImportError:
        verify_release = None

    return WorkflowDependencies(
        build_request_plan=build_bootstrap_request_plan,
        load_source_catalog=load_source_catalog,
        audit_snapshot=audit_snapshot,
        materialize_request=materialize_snapshot_request,
        parse_raw=parse_snapshot_raw,
        deduplicate_observations=deduplicate_observations,
        validate_observations=validate_observations,
        reconcile=reconcile_bootstrap,
        build_draw_records=build_draw_records,
        build_quality_report=build_bootstrap_quality_report,
        expected_reparsed_counts=EXPECTED_REPARSED_COUNTS,
        transform_observations=transform_observations,
        compare_no_change=compare_no_change,
        verify_release=verify_release,
    )


class WorkflowFailure(RuntimeError):
    def __init__(self, exit_code: int, error_code: str, message: str) -> None:
        self.exit_code = exit_code
        self.error_code = error_code
        super().__init__(message)


def _classify_failure(exc: Exception) -> WorkflowFailure:
    if isinstance(exc, WorkflowFailure):
        return exc
    if isinstance(exc, LivePolicyError):
        return WorkflowFailure(exc.exit_code, exc.category.upper(), str(exc))
    if isinstance(exc, PublishDestinationExistsError):
        return WorkflowFailure(4, "RELEASE_ID_EXISTS", str(exc))
    if isinstance(exc, (LockUnavailable, RecoveryConflict)):
        return WorkflowFailure(6, "CONCURRENT_WRITER_CONFLICT", str(exc))
    if isinstance(exc, ReplayContractError):
        return WorkflowFailure(5, "REPLAY_MISMATCH", str(exc))
    if isinstance(exc, RawHashMismatchError):
        return WorkflowFailure(5, "RAW_HASH_MISMATCH", str(exc))
    if isinstance(exc, VerifyContractError):
        return WorkflowFailure(4, "VERIFY_CONTRACT_ERROR", str(exc))
    if isinstance(exc, DeltaOutsideG2Scope):
        return WorkflowFailure(4, "INCREMENTAL_DELTA_OUTSIDE_G2_SCOPE", str(exc))
    if isinstance(exc, PublishError):
        return WorkflowFailure(6, "PUBLISH_FAILED", str(exc))
    if isinstance(exc, ContractViolation):
        detail = str(exc).lower()
        if any(token in detail for token in ("sha-256 mismatch", "raw evidence", "staged raw", "snapshot required files", "capture manifest", "request-event audit")):
            return WorkflowFailure(5, "SNAPSHOT_FILE_MISMATCH", str(exc))
        if any(token in detail for token in ("source catalog", "not approved", "source/game", "unsupported game", "captured source absent")):
            return WorkflowFailure(4, "SOURCE_POLICY_FAILED", str(exc))
        return WorkflowFailure(2, "CONTRACT_VIOLATION", str(exc))
    if isinstance(exc, (FileNotFoundError, OSError)):
        return WorkflowFailure(5, "SNAPSHOT_FILE_MISMATCH", str(exc))
    if isinstance(exc, ValueError):
        return WorkflowFailure(2, "DATA_QUALITY_FAILED", str(exc))
    return WorkflowFailure(10, "UNCLASSIFIED_FAILURE", f"{type(exc).__name__}: {exc}")


def classify_failure(exc: Exception) -> WorkflowFailure:
    return _classify_failure(exc)


def _prestart_control_result(
    arguments: BootstrapArguments | IncrementalArguments, failure: WorkflowFailure,
) -> dict[str, Any]:
    """Structured zero-run boundary result; it deliberately claims no run artifacts."""
    return {
        "preflight_result_schema_version": "1.0.0",
        "mode": arguments.mode,
        "source_mode": arguments.source_mode,
        "status": "interrupted" if failure.exit_code == 6 else "rejected",
        "exit_code": failure.exit_code,
        "error_code": failure.error_code,
        "message": str(failure),
        "request_stats": {
            "planned": 0, "started": 0, "succeeded": 0, "failed": 0, "not_started": 0,
        },
    }


def _error_detail(
    layout: RunLayout,
    failure: WorkflowFailure,
    *,
    request_id: str | None = None,
    attempt: int | None = None,
) -> tuple[Path, str]:
    if request_id is None:
        path = layout.run_root / "errors" / f"{failure.error_code.lower()}.json"
    else:
        path = layout.run_root / "errors" / request_id / f"attempt-{attempt}.json"
    atomic_write_json(path, {"error_code": failure.error_code, "message": str(failure)})
    return path, layout.ref(path)


def _write_failed_quality(layout: RunLayout, preflight: BootstrapPreflight | IncrementalPreflight, failure: WorkflowFailure, clock: Callable[[], str]) -> None:
    if layout.quality_report.exists():
        return
    atomic_write_json(layout.quality_report, {
        "quality_schema_version": "1.0.0",
        "run_id": preflight.arguments.run_id,
        "decision": "FAIL",
        "deterministic": {
            "counts": {},
            "checks": [],
            "input_hashes": {},
            "output_hashes": {},
            "blocking_reason_codes": [failure.error_code],
        },
        "generated_at_utc": clock(),
    })


def _finalize_run_hashes(layout: RunLayout, clock: Callable[[], str]) -> None:
    roles = {
        layout.manifest: "manifest", layout.events: "event", layout.quality_report: "quality", layout.result: "result"
    }
    for path, role in ((layout.observations, "observation"), (layout.reconciliation, "reconciliation"), (layout.candidate_draws, "candidate")):
        if path.is_file():
            roles[path] = role
    for path in sorted(layout.raw_root.rglob("*")):
        if path.is_file():
            roles[path] = "raw"
    config_root = layout.run_root / "config"
    if config_root.is_dir():
        for path in sorted(config_root.rglob("*")):
            if path.is_file():
                roles[path] = "config"
    errors_root = layout.run_root / "errors"
    if errors_root.is_dir():
        for path in sorted(errors_root.rglob("*")):
            if path.is_file():
                roles[path] = "error"
    entries = []
    for path, role in sorted(roles.items(), key=lambda item: layout.ref(item[0])):
        if path.is_file():
            entries.append({"path": layout.ref(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size, "role": role})
    atomic_write_json(layout.hashes, {
        "hash_manifest_schema_version": "1.0.0",
        "hash_profile": "sha256-file-manifest-v1",
        "generated_at_utc": clock(),
        "entries": entries,
    })


def _canonical_result_hashes(layout: RunLayout) -> dict[str, str]:
    paths = {
        "run_manifest": layout.manifest, "events": layout.events,
        "observations": layout.observations, "reconciliation": layout.reconciliation,
        "candidate_draws": layout.candidate_draws, "quality_report": layout.quality_report,
    }
    return {key: sha256_file(path) for key, path in paths.items() if path.is_file()}


def _apply_incremental_decision_counters(
    counters: RunCounters, decision: Any, *, parsed: int, valid: int,
) -> None:
    """Project the authoritative incremental decision counts into RunResult."""
    counts = decision.quality["deterministic"]["counts"]
    counters.parsed = parsed
    counters.valid = valid
    counters.candidates = len(decision.reconciliation)
    counters.eligible = int(counts["draws"])
    for key in ("added", "revised", "unchanged", "conflict", "unresolved"):
        setattr(counters, key, int(counts[key]))


def _persist_run_config(
    layout: RunLayout, preflight: BootstrapPreflight | IncrementalPreflight,
) -> None:
    config_root = layout.run_root / "config"
    config_root.mkdir(exist_ok=False)
    expected = {item["ref"]: item["sha256"] for item in preflight.manifest["config_files"]}
    for name, payload in preflight.config_payloads:
        path = config_root / name
        relative = f"config/{name}"
        if expected.get(relative) != sha256_bytes(payload):
            raise WorkflowFailure(5, "CONFIG_HASH_MISMATCH", f"frozen config bytes differ for {relative}")
        atomic_write_bytes(path, payload)


def _content_address_live_raw(layout: RunLayout, request: Mapping[str, Any], materialized: Mapping[str, Any]) -> dict[str, Any]:
    source = Path(materialized["raw_path"])
    digest = str(materialized["raw_sha256"])
    destination = layout.raw_root / str(request["source_id"]) / str(request["game"]) / "sha256" / f"{digest}.raw"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != digest:
            raise RawHashMismatchError(f"content-addressed destination mismatch: {destination}")
        source.unlink(missing_ok=True)
    else:
        os.replace(source, destination)
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())
    if sha256_file(destination) != digest:
        raise RawHashMismatchError(f"live raw hash mismatch: {destination}")
    return {**materialized, "raw_path": destination, "raw_ref": destination.relative_to(layout.run_root).as_posix()}


def _execute_bootstrap_once(
    arguments: BootstrapArguments,
    *,
    dependencies: WorkflowDependencies | None = None,
) -> tuple[int, dict[str, Any]]:
    deps = dependencies or default_dependencies()
    preflight = prepare_bootstrap(
        arguments, clock=deps.clock, build_request_plan=deps.build_request_plan,
        load_source_catalog=deps.load_source_catalog,
    )
    layout = RunLayout(arguments.artifacts_root.resolve(), arguments.run_id)
    layout.create()
    write_once_json(layout.manifest, preflight.manifest)
    _persist_run_config(layout, preflight)
    events = EventLog(layout.events, arguments.run_id, deps.clock)
    counters = RunCounters(planned=len(preflight.request_plan))
    error_refs: list[str] = []
    publication_token: PublicationToken | None = None
    publication_rolled_back = False
    commit_checkpoint = None
    try:
        events.append("run_planned")
        events.append("run_started")
        observations_all: list[dict[str, Any]] = []
        materialized_refs: list[str] = []
        for request in preflight.request_plan:
            events.append(
                "request_started", request_id=request["request_id"], attempt=1,
                source_id=request["source_id"], game=request["game"],
            )
            counters.started += 1
            try:
                materialized = deps.materialize_request(request, arguments.phase0_snapshot.resolve(), layout.raw_root)
                enriched_request = dict(request)
                enriched_request["provenance"] = materialized
                rows = deps.parse_raw(enriched_request, materialized["raw_path"], publisher_id=request["publisher_id"])
                observations_all.extend(rows)
                materialized_refs.append(materialized["raw_ref"])
                counters.succeeded += 1
                events.append(
                    "request_succeeded", request_id=request["request_id"], attempt=1,
                    source_id=request["source_id"], game=request["game"], artifact_ref=materialized["raw_ref"],
                )
            except Exception as exc:
                failure = _classify_failure(exc)
                counters.failed += 1
                _, error_ref = _error_detail(layout, failure)
                error_refs.append(error_ref)
                events.append(
                    "request_failed", request_id=request["request_id"], attempt=1,
                    source_id=request["source_id"], game=request["game"],
                    error_code=failure.error_code, error_detail_ref=error_ref,
                )
                raise failure
        input_hashes = {
            "artifact_hashes": preflight.manifest["bootstrap_snapshot"]["artifact_hashes_sha256"],
            "source_catalog": next(item["sha256"] for item in preflight.manifest["config_files"] if item["ref"].endswith("source-catalog.json")),
            "collection_policy": next(item["sha256"] for item in preflight.manifest["config_files"] if item["ref"].endswith("collection-policy.json")),
            "run_manifest": sha256_file(layout.manifest),
        }
        snapshot_audit = deps.audit_snapshot(arguments.phase0_snapshot.resolve())
        input_hashes.update({
            "canonical": snapshot_audit["canonical_sha256"],
            "capture_manifest": snapshot_audit["capture_manifest_sha256"],
            "request_events": snapshot_audit["request_events_sha256"],
        })
        if deps.transform_observations is None:
            raise WorkflowFailure(10, "TRANSFORM_API_UNAVAILABLE", "shared transform_observations API is unavailable")
        transformed = deps.transform_observations(
            observations_all=observations_all,
            snapshot_root=arguments.phase0_snapshot.resolve(),
            source_catalog=preflight.source_catalog,
            collection_policy=preflight.collection_policy,
            run_id=arguments.run_id,
            input_hashes=input_hashes,
            generated_at_utc=deps.clock(),
        )
        observations_all = list(transformed.observations_all)
        selected = list(transformed.observations_selected)
        reconciliation = list(transformed.reconciliation)
        draws = list(transformed.draws)
        output_hashes = dict(transformed.output_hashes)
        quality = dict(transformed.quality_report)
        counters.parsed = len(observations_all)
        counters.valid = len(selected)
        counters.candidates = len(reconciliation)
        counters.eligible = len(draws)
        counters.added = len(draws)
        atomic_write_jsonl(layout.observations, observations_all, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))
        atomic_write_jsonl(layout.reconciliation, reconciliation, sort_keys=("game", "issue_id"))
        atomic_write_jsonl(layout.candidate_draws, draws, sort_keys=("game", "issue_id", "revision_id"))
        if output_hashes != {
            "draws": sha256_file(layout.candidate_draws),
            "run_observations": sha256_file(layout.observations),
            "release_observations": output_hashes.get("release_observations"),
            "reconciliation": sha256_file(layout.reconciliation),
        }:
            raise WorkflowFailure(5, "TRANSFORM_HASH_MISMATCH", "shared transform hashes do not match staged run artifacts")
        atomic_write_json(layout.quality_report, quality)
        if quality.get("decision") != "PASS":
            raise WorkflowFailure(2, "QUALITY_GATE_FAILED", "bootstrap quality decision is not PASS")
        commit_checkpoint = events.checkpoint()
        publication_token = publish_release(
            artifacts_root=layout.artifacts_root, run_root=layout.run_root, run_id=arguments.run_id,
            release_id=arguments.release_id, previous_release_id=preflight.previous_release_id,
            manifest_sha256=sha256_file(layout.manifest),
            schema_bundle_sha256=preflight.manifest["schema_bundle_sha256"],
            pipeline_bundle_sha256=preflight.manifest["pipeline_bundle_sha256"],
            draws=draws, observations=selected, quality_report_path=layout.quality_report,
            created_at_utc=deps.clock(),
        )
        published_manifest = load_json(
            publication_token.artifacts_root / "releases" / publication_token.release_id / "manifest.json"
        )
        if published_manifest.get("observations_sha256") != output_hashes["release_observations"]:
            raise PublishError("published observations hash does not match precomputed selected observations")
        events.append("run_published", artifact_ref=f"releases/{arguments.release_id}/manifest.json")
        publication_token.journal.advance("RUN_TERMINAL", updated_at_utc=deps.clock())
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=arguments.run_id, status="published", exit_code=0,
            started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=arguments.release_id,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events), quality_report_ref=layout.ref(layout.quality_report),
            error_refs=[], counters=counters,
        )
        write_once_json(layout.result, result)
        publication_token.journal.advance("RESULT_WRITTEN", updated_at_utc=deps.clock())
        _finalize_run_hashes(layout, deps.clock)
        publication_token.journal.advance("COMPLETED", updated_at_utc=deps.clock())
        return 0, result
    except Exception as exc:
        failure = _classify_failure(exc)
        result_status = "rejected"
        terminal_event = "run_rejected"
        if publication_token is not None:
            recovery_root = layout.run_root / "recovery"
            recovery_root.mkdir(parents=True, exist_ok=True)
            for path in (layout.result, layout.hashes):
                if path.exists():
                    os.replace(path, recovery_root / ("uncommitted-" + path.name))
            try:
                if commit_checkpoint is None:
                    raise PublishError("post-publish checkpoint is missing")
                events.restore(commit_checkpoint, recovery_root / "uncommitted-events-tail.jsonl")
                rollback_publication(publication_token)
                publication_rolled_back = True
            except Exception as rollback_exc:
                failure = WorkflowFailure(10, "ROLLBACK_PRECONDITION_FAILED", f"{failure}; rollback: {rollback_exc}")
                result_status = "interrupted"
                terminal_event = "run_interrupted"
        if not error_refs or not any(failure.error_code.lower() in ref for ref in error_refs):
            _, error_ref = _error_detail(layout, failure)
            error_refs.append(error_ref)
        _write_failed_quality(layout, preflight, failure, deps.clock)
        try:
            events.append(terminal_event, error_code=failure.error_code, error_detail_ref=error_refs[-1])
        except Exception as event_exc:
            print(f"failed to append terminal event: {event_exc}", file=sys.stderr)
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=arguments.run_id, status=result_status, exit_code=failure.exit_code,
            started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=None,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events), quality_report_ref=layout.ref(layout.quality_report),
            error_refs=error_refs, counters=counters,
        )
        write_once_json(layout.result, result)
        _finalize_run_hashes(layout, deps.clock)
        if publication_token is not None and publication_rolled_back:
            publication_token.journal.complete_recovery(
                updated_at_utc=deps.clock(), quarantined=[], status="interrupted",
            )
        return failure.exit_code, result


def execute_bootstrap(
    arguments: BootstrapArguments, *, dependencies: WorkflowDependencies | None = None,
) -> tuple[int, dict[str, Any]]:
    validate_bootstrap_arguments(arguments)
    deps = dependencies or default_dependencies()
    escaped: Exception | None = None
    try:
        with RunLock(arguments.artifacts_root.resolve(), arguments.run_id):
            recover_stale_publications(arguments.artifacts_root.resolve(), clock=deps.clock)
            recover_stale_runs(arguments.artifacts_root.resolve(), clock=deps.clock)
            return _execute_bootstrap_once(arguments, dependencies=deps)
    except Exception as exc:
        escaped = _classify_failure(exc)
        if not (arguments.artifacts_root.resolve() / "runs" / arguments.run_id).exists():
            if escaped.exit_code == 6:
                return 6, _prestart_control_result(arguments, escaped)
            raise escaped from exc
    # Initialization faults can escape before an EventLog exists. Once the
    # owner lock is released, the same recovery path closes or quarantines it.
    recover_stale_runs(arguments.artifacts_root.resolve(), clock=deps.clock)
    result_path = arguments.artifacts_root.resolve() / "runs" / arguments.run_id / "run-result.json"
    if result_path.is_file():
        failure = _classify_failure(escaped or RuntimeError("bootstrap initialization failed"))
        return failure.exit_code, load_json(result_path)
    assert escaped is not None
    raise escaped


def _execute_incremental_once(
    arguments: IncrementalArguments,
    *,
    dependencies: WorkflowDependencies | None = None,
) -> tuple[int, dict[str, Any]]:
    from .steps.incremental_engine import build_incremental_release

    deps = dependencies or default_dependencies()
    preflight = prepare_incremental(
        arguments, clock=deps.clock, build_request_plan=deps.build_request_plan,
        load_source_catalog=deps.load_source_catalog,
    )
    layout = RunLayout(arguments.artifacts_root.resolve(), arguments.run_id)
    layout.create()
    write_once_json(layout.manifest, preflight.manifest)
    _persist_run_config(layout, preflight)
    events = EventLog(layout.events, arguments.run_id, deps.clock)
    counters = RunCounters(planned=len(preflight.request_plan))
    error_refs: list[str] = []
    publication_token: PublicationToken | None = None
    commit_checkpoint = None
    try:
        events.append("run_planned")
        events.append("run_started")
        if deps.verify_release is None:
            raise WorkflowFailure(10, "VERIFY_API_UNAVAILABLE", "verify_release API is unavailable")
        deps.verify_release(
            artifacts_root=arguments.artifacts_root.resolve(),
            release_id=preflight.previous_release_id,
            snapshot_root_override=arguments.snapshot_root.resolve(),
        )
        parsed: list[dict[str, Any]] = []
        new_raw_paths: dict[str, Path] = {}
        for request in preflight.request_plan:
            events.append(
                "request_started", request_id=request["request_id"], attempt=1,
                source_id=request["source_id"], game=request["game"],
            )
            counters.started += 1
            try:
                materialized = deps.materialize_request(request, arguments.snapshot_root.resolve(), layout.raw_root)
                new_raw_paths[materialized["raw_ref"]] = Path(materialized["raw_path"])
                enriched = dict(request)
                enriched["provenance"] = materialized
                parsed.extend(deps.parse_raw(enriched, materialized["raw_path"], publisher_id=request["publisher_id"]))
                counters.succeeded += 1
                events.append(
                    "request_succeeded", request_id=request["request_id"], attempt=1,
                    source_id=request["source_id"], game=request["game"], artifact_ref=materialized["raw_ref"],
                )
            except Exception as exc:
                failure = _classify_failure(exc)
                counters.failed += 1
                _, error_ref = _error_detail(layout, failure)
                error_refs.append(error_ref)
                events.append(
                    "request_failed", request_id=request["request_id"], attempt=1,
                    source_id=request["source_id"], game=request["game"],
                    error_code=failure.error_code, error_detail_ref=error_ref,
                )
                raise failure
        snapshot_audit = deps.audit_snapshot(arguments.snapshot_root.resolve())
        input_hashes = {
            "artifact_hashes": sha256_file(arguments.snapshot_root.resolve() / "artifact-hashes.json"),
            "source_catalog": sha256_file(preflight.source_catalog_path),
            "collection_policy": sha256_file(preflight.collection_policy_path),
            "run_manifest": sha256_file(layout.manifest),
            "canonical": snapshot_audit["canonical_sha256"],
            "capture_manifest": snapshot_audit["capture_manifest_sha256"],
            "request_events": snapshot_audit["request_events_sha256"],
        }
        if deps.transform_observations is None:
            raise WorkflowFailure(10, "G2_API_UNAVAILABLE", "shared transform API is unavailable")
        transformed = deps.transform_observations(
            observations_all=parsed, snapshot_root=arguments.snapshot_root.resolve(),
            source_catalog=preflight.source_catalog, collection_policy=preflight.collection_policy,
            run_id=arguments.run_id, input_hashes=input_hashes, generated_at_utc=deps.clock(),
        )
        current_draws = [json.loads(line) for line in (preflight.current_release_root / "draws.jsonl").read_text(encoding="utf-8").splitlines() if line]
        current_observations = [json.loads(line) for line in (preflight.current_release_root / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line]
        current_manifest = load_json(preflight.current_release_root / "manifest.json")
        predecessor_run = layout.artifacts_root / "runs" / current_manifest["input_run_id"]
        current_raw_paths = {row["raw_ref"]: predecessor_run / row["raw_ref"] for row in current_observations}
        current_raw_hashes = {ref: sha256_file(path) for ref, path in current_raw_paths.items()}
        new_raw_hashes = {ref: sha256_file(path) for ref, path in new_raw_paths.items()}
        publishers = {
            item["source_id"]: item["publisher_id"] for item in preflight.source_catalog.get("sources", [])
        }
        normal_pair = tuple(preflight.collection_policy["normal_source_pair"])
        fallbacks = {
            (item["game"], item["issue_id"]): tuple(item["source_pair"])
            for item in preflight.collection_policy.get("approved_issue_fallbacks", [])
        }

        def snapshot_pair(game: str, issue_id: str) -> Sequence[str]:
            fallback = fallbacks.get((game, issue_id))
            return fallback or normal_pair

        decision = build_incremental_release(
            current_draws=current_draws,
            current_selected_observations=current_observations,
            new_observations=list(transformed.observations_selected),
            new_reconciliation=list(transformed.reconciliation),
            policy=preflight.collection_policy,
            source_identities=publishers,
            current_raw_hashes=current_raw_hashes,
            new_raw_hashes=new_raw_hashes,
            recheck_limit=max(1, len(current_draws) + 2),
            pair_resolver=snapshot_pair,
        )
        for item in decision.raw_lineage_copy_plan:
            ref, digest = item["raw_ref"], item["raw_sha256"]
            destination = layout.run_root / ref
            if not destination.exists() and "current_release" in item["origins"]:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(current_raw_paths[ref], destination)
            if not destination.is_file() or sha256_file(destination) != digest:
                raise RawHashMismatchError(f"snapshot lineage copy mismatch: {ref}")
        # Snapshot input is a complete, frozen view.  Preserve its audited
        # transform counts/checks (also the G2 acceptance contract), while the
        # output hashes name the append-only engine's actual release candidate.
        quality = dict(transformed.quality_report)
        quality["run_id"] = arguments.run_id
        quality["generated_at_utc"] = deps.clock()
        quality["deterministic"] = dict(quality["deterministic"])
        quality["deterministic"]["output_hashes"] = dict(decision.quality["deterministic"]["output_hashes"])
        quality["deterministic"]["output_hashes"]["run_observations"] = transformed.output_hashes["run_observations"]
        quality["deterministic"]["input_hashes"] = {
            **dict(quality["deterministic"].get("input_hashes", {})),
            "current_release_manifest": sha256_file(preflight.current_release_root / "manifest.json"),
        }
        atomic_write_jsonl(layout.observations, transformed.observations_all, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))
        atomic_write_jsonl(layout.reconciliation, decision.reconciliation, sort_keys=("game", "issue_id"))
        atomic_write_jsonl(layout.candidate_draws, decision.draws, sort_keys=("game", "issue_id", "revision_id"))
        atomic_write_json(layout.quality_report, quality)
        _apply_incremental_decision_counters(
            counters, decision,
            parsed=len(transformed.observations_all), valid=len(decision.release_observations),
        )
        if not decision.publishable:
            raise WorkflowFailure(2, "INCREMENTAL_QUALITY_BLOCKED", "snapshot incremental decision contains blocking findings")
        no_change = decision.changes.get("added", 0) == 0 and decision.changes.get("revised", 0) == 0
        if no_change:
            checkpoint = events.checkpoint()
            try:
                with no_change_guard(layout.artifacts_root, arguments.run_id, preflight.pointer_bytes):
                    events.append("run_no_change")
                    counters.artifact_hashes = _canonical_result_hashes(layout)
                    result = build_run_result(
                        run_id=arguments.run_id, mode="incremental", status="no_change", exit_code=0,
                        started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=None,
                        manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
                        quality_report_ref=layout.ref(layout.quality_report), error_refs=[], counters=counters,
                    )
                    write_once_json(layout.result, result)
                    _finalize_run_hashes(layout, deps.clock)
                return 0, result
            except Exception:
                recovery = layout.run_root / "recovery"
                recovery.mkdir(parents=True, exist_ok=True)
                for path in (layout.result, layout.hashes):
                    if path.exists():
                        os.replace(path, recovery / ("uncommitted-" + path.name))
                events.restore(checkpoint, recovery / "uncommitted-events-tail.jsonl")
                raise
        if arguments.release_id is None:
            raise WorkflowFailure(4, "RELEASE_ID_REQUIRED", "publishable snapshot incremental run requires release-id")
        commit_checkpoint = events.checkpoint()
        publication_token = publish_release(
            artifacts_root=layout.artifacts_root, run_root=layout.run_root, run_id=arguments.run_id,
            release_id=arguments.release_id, previous_release_id=preflight.previous_release_id,
            manifest_sha256=sha256_file(layout.manifest), schema_bundle_sha256=preflight.manifest["schema_bundle_sha256"],
            pipeline_bundle_sha256=preflight.manifest["pipeline_bundle_sha256"], draws=decision.draws,
            observations=decision.release_observations, quality_report_path=layout.quality_report,
            created_at_utc=deps.clock(),
        )
        events.append("run_published", artifact_ref=f"releases/{arguments.release_id}/manifest.json")
        publication_token.journal.advance("RUN_TERMINAL", updated_at_utc=deps.clock())
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=arguments.run_id, mode="incremental", status="published", exit_code=0,
            started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=arguments.release_id,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
            quality_report_ref=layout.ref(layout.quality_report), error_refs=[], counters=counters,
        )
        write_once_json(layout.result, result)
        publication_token.journal.advance("RESULT_WRITTEN", updated_at_utc=deps.clock())
        _finalize_run_hashes(layout, deps.clock)
        publication_token.journal.advance("COMPLETED", updated_at_utc=deps.clock())
        return 0, result
    except Exception as exc:
        failure = _classify_failure(exc)
        if not error_refs or not any(failure.error_code.lower() in ref for ref in error_refs):
            _, error_ref = _error_detail(layout, failure)
            error_refs.append(error_ref)
        _write_failed_quality(layout, preflight, failure, deps.clock)
        try:
            events.append("run_rejected", error_code=failure.error_code, error_detail_ref=error_refs[-1])
        except Exception as event_exc:
            print(f"failed to append terminal event: {event_exc}", file=sys.stderr)
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=arguments.run_id, mode="incremental", status="rejected", exit_code=failure.exit_code,
            started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=None,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
            quality_report_ref=layout.ref(layout.quality_report), error_refs=error_refs, counters=counters,
        )
        write_once_json(layout.result, result)
        _finalize_run_hashes(layout, deps.clock)
        return failure.exit_code, result


def _execute_live_incremental_once(
    arguments: IncrementalArguments, *, dependencies: WorkflowDependencies,
) -> tuple[int, dict[str, Any]]:
    from .steps.incremental_engine import build_incremental_release
    from .steps.live import fetch_to_raw
    from .steps.parse import parse_versioned_raw

    deps = dependencies
    preflight = prepare_incremental(
        arguments, clock=deps.clock, build_request_plan=deps.build_request_plan,
        load_source_catalog=deps.load_source_catalog,
    )
    layout = RunLayout(arguments.artifacts_root.resolve(), arguments.run_id)
    layout.create()
    write_once_json(layout.manifest, preflight.manifest)
    _persist_run_config(layout, preflight)
    events = EventLog(layout.events, arguments.run_id, deps.clock, event_schema_version="1.3.0")
    counters = RunCounters(planned=len(preflight.request_plan))
    error_refs: list[str] = []
    publication_token: PublicationToken | None = None
    publication_rolled_back = False
    publication_quarantine: list[str] = []
    commit_checkpoint = None
    try:
        events.append("run_planned")
        events.append("run_started")
        throttle_root = layout.artifacts_root / ".host-throttle"
        observations: list[dict[str, Any]] = []
        new_raw_paths: dict[str, Path] = {}

        def perform(
            request: dict[str, Any], *, discovery_body: bytes | None = None, parse: bool = True,
            validate_rows: Callable[[Mapping[str, Any], list[Mapping[str, Any]]], None] | None = None,
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            counters.started += 1
            network = preflight.collection_policy["network_policy"]
            maximum_attempts = int(network["max_attempts_per_request"])
            retry_delay = float(network["retry_backoff_seconds"])
            for attempt in range(1, maximum_attempts + 1):
                events.append("request_started", request_id=request["request_id"], attempt=attempt,
                              source_id=request["source_id"], game=request["game"])
                try:
                    fetched = fetch_to_raw(
                        request, preflight.collection_policy, layout.raw_root, throttle_root,
                        gd_discovery_body=discovery_body,
                    )
                    materialized = _content_address_live_raw(layout, request, fetched)
                    captured = deps.clock()
                    new_raw_paths[materialized["raw_ref"]] = materialized["raw_path"]
                    rows: list[dict[str, Any]] = []
                    if parse:
                        enriched = {**request, "provenance": {
                            "url": request["url"], "captured_at_utc": captured,
                            "raw_ref": materialized["raw_ref"], "raw_sha256": materialized["raw_sha256"],
                        }}
                        rows = parse_versioned_raw(
                            enriched, materialized["raw_path"], publisher_id=request["publisher_id"],
                            parser_id=request["parser_id"], parser_version=request["parser_version"],
                        )
                        if validate_rows is not None:
                            validate_rows(request, rows)
                    events.append("request_succeeded", request_id=request["request_id"], attempt=attempt,
                                  source_id=request["source_id"], game=request["game"],
                                  artifact_ref=materialized["raw_ref"])
                    counters.succeeded += 1
                    return materialized, rows
                except Exception as exc:
                    failure = _classify_failure(exc)
                    _, ref = _error_detail(
                        layout, failure, request_id=request["request_id"], attempt=attempt,
                    )
                    error_refs.append(ref)
                    events.append("request_failed", request_id=request["request_id"], attempt=attempt,
                                  source_id=request["source_id"], game=request["game"],
                                  error_code=failure.error_code, error_detail_ref=ref)
                    retryable = isinstance(exc, LivePolicyError) and exc.retryable
                    if retryable and attempt < maximum_attempts:
                        time.sleep(retry_delay)
                        continue
                    counters.failed += 1
                    raise failure
            raise AssertionError("bounded live request loop exhausted without a terminal outcome")

        for request in preflight.request_plan:
            _, rows = perform(request)
            observations.extend(rows)

        current_draws = [json.loads(line) for line in (preflight.current_release_root / "draws.jsonl").read_text(encoding="utf-8").splitlines() if line]
        current_observations = [json.loads(line) for line in (preflight.current_release_root / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line]
        current_manifest = load_json(preflight.current_release_root / "manifest.json")
        predecessor_run = layout.artifacts_root / "runs" / current_manifest["input_run_id"]
        current_raw_paths = {row["raw_ref"]: predecessor_run / row["raw_ref"] for row in current_observations}
        current_raw_hashes = {ref: sha256_file(path) for ref, path in current_raw_paths.items()}
        new_raw_hashes = {ref: sha256_file(path) for ref, path in new_raw_paths.items()}
        decision = build_incremental_release(
            current_draws=current_draws, current_selected_observations=current_observations,
            new_observations=observations, policy=preflight.collection_policy,
            current_raw_hashes=current_raw_hashes, new_raw_hashes=new_raw_hashes,
            recheck_limit=20,
        )
        for item in decision.raw_lineage_copy_plan:
            ref, digest = item["raw_ref"], item["raw_sha256"]
            if "current_release" in item["origins"]:
                source, destination = current_raw_paths[ref], layout.run_root / ref
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(source, destination)
                if sha256_file(destination) != digest:
                    raise RawHashMismatchError(f"lineage copy mismatch: {ref}")
        quality = {"quality_schema_version": "1.0.0", "run_id": arguments.run_id,
                   **decision.quality, "generated_at_utc": deps.clock()}
        quality["deterministic"] = {
            **quality["deterministic"],
            "checks": ["live_policy", "effective_request_plan", "publisher_agreement", "raw_lineage"],
            "input_hashes": {
                "live_source_policy": preflight.manifest["config_files"][0]["sha256"],
                "run_manifest": sha256_file(layout.manifest),
                "current_release_manifest": sha256_file(preflight.current_release_root / "manifest.json"),
            },
        }
        atomic_write_jsonl(layout.observations, decision.run_observations, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))
        atomic_write_jsonl(layout.reconciliation, decision.reconciliation, sort_keys=("game", "issue_id"))
        atomic_write_jsonl(layout.candidate_draws, decision.draws, sort_keys=("game", "issue_id", "revision_id"))
        atomic_write_json(layout.quality_report, quality)
        _apply_incremental_decision_counters(
            counters, decision, parsed=len(observations), valid=len(observations),
        )
        if not decision.publishable:
            raise WorkflowFailure(2, "INCREMENTAL_QUALITY_BLOCKED", "incremental decision contains blocking findings")
        no_change = decision.changes.get("added", 0) == 0 and decision.changes.get("revised", 0) == 0
        if no_change:
            checkpoint = events.checkpoint()
            try:
                with no_change_guard(layout.artifacts_root, arguments.run_id, preflight.pointer_bytes):
                    events.append("run_no_change")
                    counters.artifact_hashes = _canonical_result_hashes(layout)
                    result = build_run_result(
                        run_id=arguments.run_id, mode="incremental", status="no_change", exit_code=0,
                        started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=None,
                        manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
                        quality_report_ref=layout.ref(layout.quality_report), error_refs=[], counters=counters,
                    )
                    validate_live_event_stream(preflight.manifest, [json.loads(line) for line in layout.events.read_text(encoding="utf-8").splitlines()], result)
                    write_once_json(layout.result, result)
                    _finalize_run_hashes(layout, deps.clock)
                    return 0, result
            except Exception:
                recovery = layout.run_root / "recovery"
                recovery.mkdir(parents=True, exist_ok=True)
                for path in (layout.result, layout.hashes):
                    if path.exists():
                        destination = recovery / ("uncommitted-" + path.name)
                        if destination.exists():
                            raise WorkflowFailure(10, "PARTIAL_COMMIT_CONFLICT", f"partial commit evidence exists: {destination}")
                        os.replace(path, destination)
                events.restore(checkpoint, recovery / "uncommitted-events-tail.jsonl")
                raise
        release_id = arguments.release_id
        if release_id is None:
            raise WorkflowFailure(4, "RELEASE_ID_REQUIRED", "publishable incremental run requires release-id")
        commit_checkpoint = events.checkpoint()
        publication_token = publish_release(
            artifacts_root=layout.artifacts_root, run_root=layout.run_root, run_id=arguments.run_id,
            release_id=release_id, previous_release_id=preflight.previous_release_id,
            manifest_sha256=sha256_file(layout.manifest), schema_bundle_sha256=preflight.manifest["schema_bundle_sha256"],
            pipeline_bundle_sha256=preflight.manifest["pipeline_bundle_sha256"], draws=decision.draws,
            observations=decision.release_observations, quality_report_path=layout.quality_report, created_at_utc=deps.clock(),
        )
        events.append("run_published", artifact_ref=f"releases/{release_id}/manifest.json")
        publication_token.journal.advance("RUN_TERMINAL", updated_at_utc=deps.clock())
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=arguments.run_id, mode="incremental", status="published", exit_code=0,
            started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=release_id,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
            quality_report_ref=layout.ref(layout.quality_report), error_refs=[], counters=counters,
        )
        validate_live_event_stream(preflight.manifest, [json.loads(line) for line in layout.events.read_text(encoding="utf-8").splitlines()], result)
        write_once_json(layout.result, result)
        publication_token.journal.advance("RESULT_WRITTEN", updated_at_utc=deps.clock())
        _finalize_run_hashes(layout, deps.clock)
        publication_token.journal.advance("COMPLETED", updated_at_utc=deps.clock())
        return 0, result
    except Exception as exc:
        failure = _classify_failure(exc)
        result_status = "rejected"
        terminal_event = "run_rejected"
        result_exit_code: int | None = failure.exit_code
        if publication_token is not None:
            recovery_root = layout.run_root / "recovery"
            recovery_root.mkdir(parents=True, exist_ok=True)
            for path in (layout.result, layout.hashes):
                if path.exists():
                    destination = recovery_root / ("uncommitted-" + path.name)
                    if destination.exists():
                        failure = WorkflowFailure(10, "PARTIAL_COMMIT_CONFLICT", f"partial commit evidence exists: {destination}")
                        break
                    os.replace(path, destination)
            try:
                if commit_checkpoint is None:
                    raise PublishError("post-publish checkpoint is missing")
                events.restore(commit_checkpoint, recovery_root / "uncommitted-events-tail.jsonl")
                recovered_release = rollback_publication(publication_token)
                publication_rolled_back = True
                publication_quarantine = [
                    recovered_release.relative_to(layout.artifacts_root).as_posix(),
                    (layout.run_root / "recovery" / "published-projection").relative_to(layout.artifacts_root).as_posix(),
                ]
            except Exception as rollback_exc:
                failure = WorkflowFailure(10, "ROLLBACK_PRECONDITION_FAILED", f"{failure}; rollback: {rollback_exc}")
                result_status = "interrupted"
                terminal_event = "run_interrupted"
                result_exit_code = None
        if not error_refs or not any(failure.error_code.lower() in ref for ref in error_refs):
            _, ref = _error_detail(layout, failure)
            error_refs.append(ref)
        _write_failed_quality(layout, preflight, failure, deps.clock)
        events.append(terminal_event, error_code=failure.error_code, error_detail_ref=error_refs[-1])
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=arguments.run_id, mode="incremental", status=result_status, exit_code=result_exit_code,
            started_at_utc=preflight.started_at_utc, completed_at_utc=deps.clock(), release_id=None,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
            quality_report_ref=layout.ref(layout.quality_report), error_refs=error_refs, counters=counters,
        )
        validate_live_event_stream(preflight.manifest, [json.loads(line) for line in layout.events.read_text(encoding="utf-8").splitlines()], result)
        write_once_json(layout.result, result)
        _finalize_run_hashes(layout, deps.clock)
        if publication_token is not None and publication_rolled_back:
            publication_token.journal.complete_recovery(
                updated_at_utc=deps.clock(), quarantined=publication_quarantine, status="interrupted",
            )
        return failure.exit_code, result


def execute_incremental(
    arguments: IncrementalArguments,
    *,
    dependencies: WorkflowDependencies | None = None,
) -> tuple[int, dict[str, Any]]:
    deps = dependencies or default_dependencies()
    # This gate is intentionally before RunLock, recovery, layout, or any
    # artifacts-root creation. Invalid/expired live policy has zero side effect.
    validate_incremental_arguments(arguments)
    validate_live_preflight_policy(arguments)
    escaped: Exception | None = None
    try:
        with RunLock(arguments.artifacts_root.resolve(), arguments.run_id):
            recover_stale_publications(arguments.artifacts_root.resolve(), clock=deps.clock)
            recover_stale_runs(arguments.artifacts_root.resolve(), clock=deps.clock)
            if arguments.source_mode == "live":
                return _execute_live_incremental_once(arguments, dependencies=deps)
            return _execute_incremental_once(arguments, dependencies=deps)
    except Exception as exc:
        escaped = _classify_failure(exc)
        if not (arguments.artifacts_root.resolve() / "runs" / arguments.run_id).exists():
            if escaped.exit_code == 6:
                return 6, _prestart_control_result(arguments, escaped)
            raise escaped from exc
    recover_stale_runs(arguments.artifacts_root.resolve(), clock=deps.clock)
    result_path = arguments.artifacts_root.resolve() / "runs" / arguments.run_id / "run-result.json"
    if result_path.is_file():
        failure = _classify_failure(escaped or RuntimeError("incremental initialization failed"))
        return failure.exit_code, load_json(result_path)
    assert escaped is not None
    raise escaped


def execute_replay(
    *, artifacts_root: Path, source_run_id: str, run_id: str, offline: bool,
    dependencies: WorkflowDependencies | None = None,
) -> tuple[int, dict[str, Any]]:
    if not offline:
        raise PreflightError("replay requires --offline")
    deps = dependencies or default_dependencies()
    layout = RunLayout(artifacts_root.resolve(), run_id)
    with replay_session(layout.artifacts_root, source_run_id) as session:
        source_manifest = load_json(session.plan.source_run_root / "run-manifest.json")
        if source_manifest.get("run_schema_version") != "1.0.0" or source_manifest.get("mode") != "bootstrap":
            raise WorkflowFailure(4, "REPLAY_PROFILE_UNSUPPORTED", "only a completed bootstrap run is replayable in this profile")
        if layout.run_root.exists():
            raise PreflightError("replay run-id already exists")
        started = deps.clock()
        replay_requests = []
        for index, request in enumerate(session.plan.requests, 1):
            replay_requests.append({
                key: value for key, value in {
                    **request, "sequence": index, "method": "SNAPSHOT",
                }.items() if key not in {"source_raw_path", "source_raw_sha256"}
            })
        manifest = {
            **source_manifest, "run_id": run_id, "started_at_utc": started,
            "artifacts_root": str(layout.artifacts_root), "source_mode": "snapshot",
            "request_plan": replay_requests, "replay_of_run_id": source_run_id,
        }
        validate_object("RunManifest", manifest)
        layout.create()
        write_once_json(layout.manifest, manifest)
        events = EventLog(layout.events, run_id, deps.clock)
        counters = RunCounters(planned=len(replay_requests))
        events.append("run_planned")
        events.append("run_started")
        source_observations = [
            json.loads(line) for line in (session.plan.source_run_root / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line
        ]
        provenance_by_ref = {row["raw_ref"]: row for row in source_observations}
        parsed: list[dict[str, Any]] = []
        for planned, replay_request in zip(session.plan.requests, replay_requests):
            events.append("request_started", request_id=replay_request["request_id"], attempt=1,
                          source_id=replay_request["source_id"], game=replay_request["game"])
            counters.started += 1
            destination = layout.run_root / replay_request["input_ref"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(planned["source_raw_path"], destination)
            if sha256_file(destination) != planned["source_raw_sha256"]:
                raise RawHashMismatchError(f"replay copied raw mismatch: {replay_request['input_ref']}")
            source_provenance = provenance_by_ref.get(replay_request["input_ref"])
            if source_provenance is None:
                raise WorkflowFailure(5, "REPLAY_PROVENANCE_MISSING", "source observation does not close to replay raw")
            enriched = {**replay_request, "provenance": {
                "url": replay_request["url"], "captured_at_utc": source_provenance["captured_at_utc"],
                "raw_ref": replay_request["input_ref"], "raw_sha256": planned["source_raw_sha256"],
            }}
            parsed.extend(deps.parse_raw(enriched, destination, publisher_id=replay_request["publisher_id"]))
            counters.succeeded += 1
            events.append("request_succeeded", request_id=replay_request["request_id"], attempt=1,
                          source_id=replay_request["source_id"], game=replay_request["game"],
                          artifact_ref=replay_request["input_ref"])
        if deps.transform_observations is None:
            raise WorkflowFailure(10, "TRANSFORM_API_UNAVAILABLE", "replay transform API is unavailable")
        snapshot_root = Path(source_manifest["bootstrap_snapshot"]["snapshot_root"])
        source_quality = load_json(session.plan.source_run_root / "quality-report.json")
        catalog = deps.load_source_catalog(session.plan.config_path("source-catalog.json"))
        policy = load_json(session.plan.config_path("collection-policy.json"))
        transformed = deps.transform_observations(
            observations_all=parsed, snapshot_root=snapshot_root, source_catalog=catalog,
            collection_policy=policy, run_id=run_id,
            input_hashes=source_quality["deterministic"]["input_hashes"], generated_at_utc=deps.clock(),
        )
        atomic_write_jsonl(layout.observations, transformed.observations_all, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))
        atomic_write_jsonl(layout.reconciliation, transformed.reconciliation, sort_keys=("game", "issue_id"))
        atomic_write_jsonl(layout.candidate_draws, transformed.draws, sort_keys=("game", "issue_id", "revision_id"))
        atomic_write_json(layout.quality_report, transformed.quality_report)
        compared = compare_deterministic_outputs(session.plan.source_run_root, layout.run_root)
        counters.parsed = len(transformed.observations_all)
        counters.valid = len(transformed.observations_selected)
        counters.candidates = len(transformed.reconciliation)
        counters.eligible = len(transformed.draws)
        counters.unchanged = len(transformed.draws)
        events.append("run_no_change")
        counters.artifact_hashes = _canonical_result_hashes(layout)
        result = build_run_result(
            run_id=run_id, mode="bootstrap", status="no_change", exit_code=0,
            started_at_utc=started, completed_at_utc=deps.clock(), release_id=None,
            manifest_ref=layout.ref(layout.manifest), events_ref=layout.ref(layout.events),
            quality_report_ref=layout.ref(layout.quality_report), error_refs=[], counters=counters,
        )
        write_once_json(layout.result, result)
        _finalize_run_hashes(layout, deps.clock)
        return 0, result


def execute_verify(
    *, artifacts_root: Path, release_id: str, snapshot_root_override: Path | None = None,
    dependencies: WorkflowDependencies | None = None,
) -> tuple[int, dict[str, Any]]:
    if not release_id or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for character in release_id):
        return 4, {"verification_schema_version": "1.0.0", "status": "FAIL", "release_id": release_id, "error_code": "INVALID_RELEASE_ID", "checks": []}
    deps = dependencies or default_dependencies()
    if deps.verify_release is None:
        return 10, {"verification_schema_version": "1.0.0", "status": "FAIL", "release_id": release_id, "error_code": "VERIFY_API_UNAVAILABLE", "checks": []}
    try:
        report = deps.verify_release(
            artifacts_root=artifacts_root.resolve(), release_id=release_id,
            snapshot_root_override=snapshot_root_override.resolve() if snapshot_root_override else None,
        )
        return 0, dict(report)
    except Exception as exc:
        code = 5 if isinstance(exc, RawHashMismatchError) else 4 if isinstance(exc, VerifyContractError) else 5
        return code, {
            "verification_schema_version": "1.0.0", "status": "FAIL", "release_id": release_id,
            "error_code": "RAW_HASH_MISMATCH" if code == 5 else "VERIFY_CONTRACT_ERROR",
            "message": str(exc), "checks": [],
        }
