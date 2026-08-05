from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from lottery_data.artifacts import validate_stable_id
from lottery_data.models import ContractViolation, distribution_file_by_suffix, validate_object
from lottery_data.serialization import bundle_sha256, sha256_bytes, sha256_file
from lottery_data.steps.live_policy import build_live_request_plan, load_live_policy


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FROZEN_SNAPSHOT_SOURCE_CATALOG_SHA256 = "b0a30f0a6c90744043cb74ed504db161b3456c9607618a522237cf28487b36fa"
_FROZEN_SNAPSHOT_COLLECTION_POLICY_SHA256 = "79c2f55d93d3458602122f51148c96073f3dc4a809f95bc3cc7041e0a983e760"


class PreflightError(ValueError):
    pass


class SnapshotConfigurationError(PreflightError, ContractViolation):
    """Frozen snapshot configuration identity failure classified as source policy."""

    def __init__(self, message: str) -> None:
        ValueError.__init__(self, message)


@dataclass(frozen=True)
class BootstrapArguments:
    mode: str
    source_mode: str
    phase0_snapshot: Path
    artifacts_root: Path
    config_root: Path | None
    run_id: str
    release_id: str
    games: tuple[str, ...] = ("ssq", "dlt")


@dataclass(frozen=True)
class BootstrapPreflight:
    arguments: BootstrapArguments
    started_at_utc: str
    source_catalog: Mapping[str, Any]
    collection_policy: Mapping[str, Any]
    source_catalog_path: Path
    collection_policy_path: Path
    request_plan: list[dict[str, Any]]
    previous_release_id: str | None
    manifest: dict[str, Any]
    config_payloads: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class IncrementalArguments:
    mode: str
    source_mode: str
    snapshot_root: Path | None
    artifacts_root: Path
    config_root: Path | None
    run_id: str
    release_id: str | None = None
    games: tuple[str, ...] = ("ssq", "dlt")


@dataclass(frozen=True)
class IncrementalPreflight:
    arguments: IncrementalArguments
    started_at_utc: str
    source_catalog: Mapping[str, Any]
    collection_policy: Mapping[str, Any]
    source_catalog_path: Path
    collection_policy_path: Path
    request_plan: list[dict[str, Any]]
    previous_release_id: str
    pointer_bytes: bytes
    current_release_root: Path
    manifest: dict[str, Any]
    config_payloads: tuple[tuple[str, bytes], ...]


def validate_bootstrap_arguments(arguments: BootstrapArguments) -> None:
    """Validate the API boundary and frozen snapshot configuration before locking."""
    if arguments.mode != "bootstrap" or arguments.source_mode != "snapshot":
        raise PreflightError("this milestone supports only run --mode bootstrap --source-mode snapshot")
    if tuple(arguments.games) != ("ssq", "dlt"):
        raise PreflightError("bootstrap requires exactly games ssq,dlt")
    try:
        validate_stable_id(arguments.run_id, "run-id")
        validate_stable_id(arguments.release_id, "release-id")
    except ValueError as exc:
        raise PreflightError(str(exc)) from exc
    from lottery_data.steps.snapshot import load_source_catalog
    _load_snapshot_configuration(arguments.config_root, load_source_catalog)


def validate_incremental_arguments(arguments: IncrementalArguments) -> None:
    """Pure API boundary validation; safe to call before locks and recovery."""
    if arguments.mode != "incremental" or arguments.source_mode not in {"snapshot", "live"}:
        raise PreflightError("incremental source-mode must be snapshot or live")
    if tuple(arguments.games) != ("ssq", "dlt"):
        raise PreflightError("incremental requires exactly games ssq,dlt")
    try:
        validate_stable_id(arguments.run_id, "run-id")
        if arguments.release_id is not None:
            validate_stable_id(arguments.release_id, "release-id")
    except ValueError as exc:
        raise PreflightError(str(exc)) from exc
    if arguments.source_mode == "snapshot" and arguments.snapshot_root is None:
        raise PreflightError("snapshot incremental requires --snapshot-root")
    if arguments.source_mode == "live" and arguments.snapshot_root is not None:
        raise PreflightError("live incremental forbids snapshot-root")
    if arguments.source_mode == "snapshot":
        from lottery_data.steps.snapshot import load_source_catalog
        _load_snapshot_configuration(arguments.config_root, load_source_catalog)


def validate_live_preflight_policy(arguments: IncrementalArguments) -> dict[str, Any] | None:
    """Run the fail-closed live policy gate before any artifacts or locks are created."""
    validate_incremental_arguments(arguments)
    if arguments.source_mode != "live":
        return None
    if arguments.mode != "incremental" or tuple(arguments.games) != ("ssq", "dlt"):
        raise PreflightError("live collection requires incremental games ssq,dlt")
    if arguments.snapshot_root is not None:
        raise PreflightError("live incremental forbids snapshot-root")
    live_path = (
        _default_resource("config/phase1/live-source-policy.json")
        if arguments.config_root is None else arguments.config_root.resolve() / "live-source-policy.json"
    )
    return load_live_policy(live_path)


def _source_repo_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[3]
    return candidate if (candidate / "pyproject.toml").is_file() else None


def _default_resource(relative: str) -> Path:
    root = _source_repo_root()
    if root is not None:
        path = root / relative
        if path.is_file():
            return path
    return distribution_file_by_suffix(f"share/autoresearch-lotte/{relative}")


def _pipeline_bundle() -> str:
    root = _source_repo_root()
    if root is not None:
        files = sorted((root / "src" / "lottery_data").rglob("*.py"))
        return bundle_sha256(files, root=root)
    installed = distribution("autoresearch-lotte")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for package_path in installed.files or ():
        normalized = PurePosixPath(str(package_path).replace("\\", "/"))
        parts = normalized.parts
        if "lottery_data" not in parts or normalized.suffix != ".py":
            continue
        index = parts.index("lottery_data")
        relative = PurePosixPath(*parts[index:]).as_posix()
        if relative in seen:
            raise PreflightError(f"duplicate installed pipeline file: {relative}")
        located = Path(package_path.locate())
        if not located.is_file():
            raise PreflightError(f"installed pipeline file is missing: {relative}")
        seen.add(relative)
        entries.append({"path": relative, "sha256": sha256_file(located)})
    if not entries:
        raise PreflightError("installed distribution contains no lottery_data Python files")
    return bundle_sha256(entries)


def _stable_ref(path: Path) -> str:
    resolved = path.resolve()
    root = _source_repo_root()
    if root is not None:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            pass
    return path.name


def _current_release_id(artifacts_root: Path) -> str | None:
    pointer = artifacts_root / "current-release.json"
    if not pointer.exists():
        return None
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
        release_id = value["release_id"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PreflightError("current-release.json is invalid") from exc
    if not isinstance(release_id, str) or not _ID.fullmatch(release_id):
        raise PreflightError("current release_id is invalid")
    return release_id


def _load_snapshot_configuration(
    config_root: Path | None,
    load_source_catalog: Callable[[Path], dict[str, Any]],
) -> tuple[Path, Path, bytes, bytes, dict[str, Any], dict[str, Any]]:
    if config_root is None:
        source_catalog_path = _default_resource("config/phase1/source-catalog.json")
        collection_policy_path = _default_resource("config/phase1/collection-policy.json")
    else:
        source_catalog_path = config_root.resolve() / "source-catalog.json"
        collection_policy_path = config_root.resolve() / "collection-policy.json"
    try:
        source_catalog_bytes = source_catalog_path.read_bytes()
        collection_policy_bytes = collection_policy_path.read_bytes()
    except OSError as exc:
        raise SnapshotConfigurationError("frozen snapshot source catalog or collection policy is missing") from exc
    if sha256_bytes(source_catalog_bytes) != _FROZEN_SNAPSHOT_SOURCE_CATALOG_SHA256:
        raise SnapshotConfigurationError("snapshot source catalog does not match the frozen configuration identity")
    if sha256_bytes(collection_policy_bytes) != _FROZEN_SNAPSHOT_COLLECTION_POLICY_SHA256:
        raise SnapshotConfigurationError("snapshot source catalog or collection policy does not match the frozen configuration identity")
    try:
        source_catalog = load_source_catalog(source_catalog_path)
        collection_policy = json.loads(collection_policy_bytes.decode("utf-8"))
        source_catalog_after = sha256_file(source_catalog_path)
        collection_policy_after = sha256_file(collection_policy_path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SnapshotConfigurationError("snapshot source catalog or collection policy is not valid stable UTF-8 JSON") from exc
    if source_catalog_after != _FROZEN_SNAPSHOT_SOURCE_CATALOG_SHA256:
        raise SnapshotConfigurationError("snapshot source catalog changed during preflight")
    if collection_policy_after != _FROZEN_SNAPSHOT_COLLECTION_POLICY_SHA256:
        raise SnapshotConfigurationError("snapshot source catalog or collection policy changed during preflight")
    if not isinstance(source_catalog, dict) or not isinstance(collection_policy, dict):
        raise SnapshotConfigurationError("snapshot source catalog and collection policy must be objects")
    return (
        source_catalog_path, collection_policy_path,
        source_catalog_bytes, collection_policy_bytes,
        source_catalog, collection_policy,
    )


def prepare_bootstrap(
    arguments: BootstrapArguments,
    *,
    clock: Callable[[], str],
    build_request_plan: Callable[[Path, Sequence[str], Mapping[str, Any]], list[dict[str, Any]]],
    load_source_catalog: Callable[[Path], dict[str, Any]],
) -> BootstrapPreflight:
    validate_bootstrap_arguments(arguments)
    snapshot = arguments.phase0_snapshot.resolve()
    if not snapshot.is_dir() or not (snapshot / "artifact-hashes.json").is_file():
        raise PreflightError("phase0 snapshot or artifact-hashes.json is missing")
    (
        source_catalog_path, collection_policy_path,
        source_catalog_bytes, collection_policy_bytes,
        source_catalog, collection_policy,
    ) = _load_snapshot_configuration(arguments.config_root, load_source_catalog)
    if (arguments.artifacts_root / "runs" / arguments.run_id).exists():
        raise PreflightError("run-id already exists")
    if (arguments.artifacts_root / "releases" / arguments.release_id).exists():
        raise PreflightError("release-id already exists")
    if (arguments.artifacts_root / arguments.release_id).exists():
        raise PreflightError("release-id root projection already exists")
    request_plan = build_request_plan(snapshot, arguments.games, source_catalog)
    if not isinstance(request_plan, list):
        raise PreflightError("bootstrap request plan must be a list")
    previous_release_id = _current_release_id(arguments.artifacts_root)
    freeze_path = _default_resource("tests/phase1/fixtures/spec/spec-bundle-freeze.json")
    if not freeze_path.is_file():
        raise PreflightError("spec bundle freeze is missing")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    schema_bundle = freeze.get("expected_bundle_sha256")
    if not isinstance(schema_bundle, str):
        raise PreflightError("spec bundle freeze is invalid")
    pipeline_bundle = _pipeline_bundle()
    started_at = clock()
    artifact_hashes_path = snapshot / "artifact-hashes.json"
    manifest = {
        "run_schema_version": "1.0.0",
        "run_id": arguments.run_id,
        "mode": "bootstrap",
        "source_mode": "snapshot",
        "started_at_utc": started_at,
        "artifacts_root": str(arguments.artifacts_root),
        "previous_release_id": previous_release_id,
        "games": list(arguments.games),
        "request_plan": request_plan,
        "config_files": [
            {"ref": "config/collection-policy.json", "sha256": sha256_bytes(collection_policy_bytes)},
            {"ref": "config/source-catalog.json", "sha256": sha256_bytes(source_catalog_bytes)},
        ],
        "schema_bundle_sha256": schema_bundle,
        "pipeline_bundle_sha256": pipeline_bundle,
        "python_version": platform.python_version(),
        "bootstrap_snapshot": {
            "snapshot_id": snapshot.name,
            "snapshot_root": str(snapshot),
            "artifact_hashes_ref": _stable_ref(artifact_hashes_path),
            "artifact_hashes_sha256": sha256_file(artifact_hashes_path),
        },
        "incremental_watermark": None,
        "publish_policy": {
            "lock_ref": ".publish.lock",
            "compare_and_swap": True,
            "atomic_release_rename": True,
            "atomic_pointer_replace": True,
        },
        "replay_of_run_id": None,
    }
    validate_object("RunManifest", manifest)
    return BootstrapPreflight(
        arguments=arguments,
        started_at_utc=started_at,
        source_catalog=source_catalog,
        collection_policy=collection_policy,
        source_catalog_path=source_catalog_path,
        collection_policy_path=collection_policy_path,
        request_plan=request_plan,
        previous_release_id=previous_release_id,
        manifest=manifest,
        config_payloads=(("collection-policy.json", collection_policy_bytes), ("source-catalog.json", source_catalog_bytes)),
    )


def prepare_incremental(
    arguments: IncrementalArguments,
    *,
    clock: Callable[[], str],
    build_request_plan: Callable[[Path, Sequence[str], Mapping[str, Any]], list[dict[str, Any]]],
    load_source_catalog: Callable[[Path], dict[str, Any]],
) -> IncrementalPreflight:
    validate_incremental_arguments(arguments)
    snapshot = arguments.snapshot_root.resolve() if arguments.snapshot_root is not None else None
    if arguments.source_mode == "snapshot" and (
        snapshot is None or not snapshot.is_dir() or not (snapshot / "artifact-hashes.json").is_file()
    ):
        raise PreflightError("snapshot-root or artifact-hashes.json is missing")
    if arguments.source_mode == "live" and snapshot is not None:
        raise PreflightError("live incremental forbids snapshot-root")
    if (arguments.artifacts_root / "runs" / arguments.run_id).exists():
        raise PreflightError("run-id already exists")
    if arguments.release_id is not None and (
        (arguments.artifacts_root / "releases" / arguments.release_id).exists()
        or (arguments.artifacts_root / arguments.release_id).exists()
    ):
        raise PreflightError("release-id already exists")
    pointer_path = arguments.artifacts_root / "current-release.json"
    if not pointer_path.is_file():
        raise PreflightError("snapshot incremental requires a current release")
    pointer_bytes = pointer_path.read_bytes()
    previous_release_id = _current_release_id(arguments.artifacts_root)
    if previous_release_id is None:
        raise PreflightError("snapshot incremental requires a current release")
    current_release_root = arguments.artifacts_root / "releases" / previous_release_id
    projection_root = arguments.artifacts_root / previous_release_id
    if not current_release_root.is_dir() or not projection_root.is_dir():
        raise PreflightError("current release or root projection is missing")
    try:
        draws = [json.loads(line) for line in (current_release_root / "draws.jsonl").read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError) as exc:
        raise PreflightError("current release draws are invalid") from exc
    latest_issue_by_game: dict[str, str] = {}
    for game in arguments.games:
        issues = [row.get("issue_id") for row in draws if row.get("game") == game]
        if not issues or any(not isinstance(issue, str) for issue in issues):
            raise PreflightError(f"current release has no valid {game} issue watermark")
        latest_issue_by_game[game] = max(issues)
    if arguments.source_mode == "live":
        live_path = (
            _default_resource("config/phase1/live-source-policy.json")
            if arguments.config_root is None else arguments.config_root.resolve() / "live-source-policy.json"
        )
        live_policy_bytes = live_path.read_bytes()
        collection_policy = load_live_policy(live_path)
        if sha256_bytes(live_policy_bytes) != sha256_file(live_path):
            raise PreflightError("live source policy changed during preflight")
        source_catalog = {"sources": collection_policy["sources"]}
        source_catalog_path = collection_policy_path = live_path
        request_plan = build_live_request_plan(collection_policy, arguments.games)
        live_schemas = [
            _default_resource("schemas/phase1/run-manifest-v1.3.schema.json"),
            _default_resource("schemas/phase1/run-event-v1.3.schema.json"),
        ]
        schema_bundle = bundle_sha256(live_schemas, root=live_schemas[0].parent)
        config_files = [{"ref": "config/live-source-policy.json", "sha256": sha256_bytes(live_policy_bytes)}]
        config_payloads = (("live-source-policy.json", live_policy_bytes),)
        run_schema_version = "1.3.0"
    else:
        (
            source_catalog_path, collection_policy_path,
            source_catalog_bytes, collection_policy_bytes,
            source_catalog, collection_policy,
        ) = _load_snapshot_configuration(arguments.config_root, load_source_catalog)
        request_plan = build_request_plan(snapshot, arguments.games, source_catalog)
        freeze = json.loads(_default_resource("tests/phase1/fixtures/spec/spec-bundle-freeze.json").read_text(encoding="utf-8"))
        schema_bundle = freeze["expected_bundle_sha256"]
        config_files = [
            {"ref": "config/collection-policy.json", "sha256": sha256_bytes(collection_policy_bytes)},
            {"ref": "config/source-catalog.json", "sha256": sha256_bytes(source_catalog_bytes)},
        ]
        config_payloads = (("collection-policy.json", collection_policy_bytes), ("source-catalog.json", source_catalog_bytes))
        run_schema_version = "1.0.0"
    started_at = clock()
    manifest = {
        "run_schema_version": run_schema_version,
        "run_id": arguments.run_id,
        "mode": "incremental",
        "source_mode": arguments.source_mode,
        "started_at_utc": started_at,
        "artifacts_root": str(arguments.artifacts_root),
        "previous_release_id": previous_release_id,
        "games": list(arguments.games),
        "request_plan": request_plan,
        "config_files": config_files,
        "schema_bundle_sha256": schema_bundle,
        "pipeline_bundle_sha256": _pipeline_bundle(),
        "python_version": platform.python_version(),
        "bootstrap_snapshot": None,
        "incremental_watermark": {
            "current_release_id": previous_release_id,
            "latest_issue_by_game": latest_issue_by_game,
            "recheck_published_issues": 20,
        },
        "publish_policy": {
            "lock_ref": ".publish.lock", "compare_and_swap": True,
            "atomic_release_rename": True, "atomic_pointer_replace": True,
        },
        "replay_of_run_id": None,
    }
    validate_object("RunManifestV1.3" if arguments.source_mode == "live" else "RunManifest", manifest)
    return IncrementalPreflight(
        arguments=arguments, started_at_utc=started_at, source_catalog=source_catalog,
        collection_policy=collection_policy, source_catalog_path=source_catalog_path,
        collection_policy_path=collection_policy_path, request_plan=request_plan,
        previous_release_id=previous_release_id, pointer_bytes=pointer_bytes,
        current_release_root=current_release_root, manifest=manifest,
        config_payloads=config_payloads,
    )
