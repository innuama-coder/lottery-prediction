"""Offline replay preparation and deterministic-output comparison."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from lottery_data.artifacts import load_json, validate_stable_id
from lottery_data.models import ContractViolation, validate_live_event_stream, validate_object
from lottery_data.serialization import canonical_json_bytes, sha256_bytes, sha256_file
from lottery_data.steps.live_policy import LIVE_POLICY_SHA256, LIVE_POLICY_V13_SHA256


class ReplayContractError(RuntimeError):
    exit_code = 5


class ReplayDeterminismError(ReplayContractError):
    pass


class ReplayNetworkForbidden(ReplayContractError):
    pass


class ReplayPublicationForbidden(ReplayContractError):
    pass


class ReplayMutationError(ReplayContractError):
    recovery_required = True

    def __init__(self, message: str, *, original_error: BaseException | None = None) -> None:
        self.original_error = original_error
        super().__init__(message)


_LEGACY_LIVE_POLICY_SHA256 = "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1"
_LIVE_POLICY_BY_RUN_SCHEMA = {
    "1.1.0": _LEGACY_LIVE_POLICY_SHA256,
    "1.2.0": LIVE_POLICY_SHA256,
    "1.3.0": LIVE_POLICY_V13_SHA256,
}


def _replay_profile(manifest: dict[str, Any]) -> str:
    version = manifest.get("run_schema_version")
    source_mode = manifest.get("source_mode")
    try:
        if source_mode == "snapshot" and version == "1.0.0":
            validate_object("RunManifest", manifest)
            return "snapshot-v1"
        if source_mode != "live" or version not in _LIVE_POLICY_BY_RUN_SCHEMA:
            raise ReplayContractError(f"unsupported or mixed replay run profile: {source_mode!r}/{version!r}")
        schema = {"1.1.0": "RunManifestV1.1", "1.2.0": "RunManifestV1.2", "1.3.0": "RunManifestV1.3"}[version]
        validate_object(schema, manifest)
    except ContractViolation as exc:
        raise ReplayContractError(str(exc)) from exc
    expected = _LIVE_POLICY_BY_RUN_SCHEMA[version]
    if manifest.get("config_files") != [{"ref": "config/live-source-policy.json", "sha256": expected}]:
        raise ReplayContractError("replay live manifest policy identity differs from its frozen profile")
    return "live-v11" if version == "1.1.0" else "live-v12"


def _live_replay_requests(
    manifest: dict[str, Any], events: list[dict[str, Any]], result: dict[str, Any], profile: str,
) -> list[tuple[dict[str, Any], str]]:
    try:
        validate_object("RunResult", result)
        validate_live_event_stream(manifest, events, result)
    except ContractViolation as exc:
        raise ReplayContractError(str(exc)) from exc
    effective = {row["request_id"]: dict(row) for row in manifest["request_plan"]}
    if profile == "live-v11":
        for event in events:
            if event.get("event_type") == "request_discovered":
                effective[event["request_id"]] = {
                    key: event[key] for key in (
                        "request_id", "source_id", "publisher_id", "game", "method", "url",
                        "request_kind", "parser_id", "parser_version",
                    )
                }
    succeeded = {
        row.get("request_id"): row.get("artifact_ref")
        for row in events if row.get("event_type") == "request_succeeded"
    }
    if set(succeeded) != set(effective) or any(not isinstance(succeeded[key], str) for key in effective):
        raise ReplayContractError("live replay requires one successful raw artifact for every effective request")
    return [(request, succeeded[request_id]) for request_id, request in effective.items()]


class OfflineReplayTransport:
    """Transport capability supplied to replay: every network operation fails."""

    def _forbidden(self, *_: object, **__: object) -> None:
        raise ReplayNetworkForbidden("network transport is disabled during replay")

    request = get = open = send = _forbidden


class DisabledReplayPublication:
    """Publication capability supplied to replay: every mutation operation fails."""

    def _forbidden(self, *_: object, **__: object) -> None:
        raise ReplayPublicationForbidden("publication is disabled during replay")

    publish_release = rollback_publication = __call__ = _forbidden


@dataclass(frozen=True)
class ReplayPlan:
    source_run_id: str
    source_run_root: Path
    requests: tuple[dict[str, Any], ...]
    config_files: tuple[dict[str, Any], ...]
    source_inventory: tuple[tuple[str, str, int, str], ...]
    network_allowed: bool = False

    def config_path(self, filename: str) -> Path:
        """Return one hash-verified run-local config by basename."""
        matches = [item["source_path"] for item in self.config_files if Path(item["ref"]).name == filename]
        if len(matches) != 1:
            raise ReplayContractError(f"replay source requires exactly one run-local config named {filename!r}")
        return matches[0]


@dataclass(frozen=True)
class ReplaySession:
    """The only capabilities handed to an offline replay executor."""

    plan: ReplayPlan
    transport: OfflineReplayTransport
    publication: DisabledReplayPublication


def _safe_ref(value: str) -> PurePosixPath:
    pure = PurePosixPath(value)
    if pure.is_absolute() or "\\" in value or ".." in pure.parts or not value.startswith("raw/"):
        raise ReplayContractError(f"unsafe replay raw ref: {value!r}")
    return pure


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayContractError(f"invalid replay JSONL: {path}") from exc


def _load_stable_json(path: Path) -> tuple[dict[str, Any], tuple[int, str]]:
    try:
        body = path.read_bytes()
        value = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayContractError(f"invalid replay JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReplayContractError(f"replay JSON root is not an object: {path}")
    return value, (len(body), sha256_bytes(body))


def _safe_run_local_path(run_root: Path, value: Any, *, prefix: str | None, label: str) -> tuple[PurePosixPath, Path]:
    if not isinstance(value, str) or not value:
        raise ReplayContractError(f"missing {label} ref")
    pure = PurePosixPath(value)
    if pure.is_absolute() or "\\" in value or ".." in pure.parts or (prefix is not None and (not pure.parts or pure.parts[0] != prefix)):
        raise ReplayContractError(f"unsafe run-local {label} ref: {value!r}")
    candidate = run_root.joinpath(*pure.parts)
    current = run_root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ReplayContractError(f"symbolic links are forbidden in replay source: {value!r}")
    try:
        candidate.resolve().relative_to(run_root.resolve())
    except (OSError, ValueError) as exc:
        raise ReplayContractError(f"run-local {label} ref escapes source run: {value!r}") from exc
    if not candidate.is_file():
        raise ReplayContractError(f"run-local {label} is missing: {value!r}")
    return pure, candidate


def _source_run_inventory(run_root: Path) -> tuple[tuple[str, str, int, str], ...]:
    if run_root.is_symlink() or not run_root.is_dir():
        raise ReplayContractError("replay source run root is missing or symbolic")
    values: list[tuple[str, str, int, str]] = []
    for path in sorted(run_root.rglob("*"), key=lambda item: item.relative_to(run_root).as_posix()):
        relative = path.relative_to(run_root).as_posix()
        if path.is_symlink():
            raise ReplayContractError(f"symbolic links are forbidden in replay source: {relative}")
        if path.is_dir():
            values.append((relative, "dir", 0, ""))
        elif path.is_file():
            values.append((relative, "file", path.stat().st_size, sha256_file(path)))
        else:
            raise ReplayContractError(f"unsupported replay source path type: {relative}")
    return tuple(values)


def _expected_inventory_paths(files: set[str]) -> set[str]:
    expected = set(files)
    for relative in files:
        pure = PurePosixPath(relative)
        for index in range(1, len(pure.parts)):
            expected.add(PurePosixPath(*pure.parts[:index]).as_posix())
    return expected


def prepare_replay(artifacts_root: Path, source_run_id: str) -> ReplayPlan:
    try:
        source_run_id = validate_stable_id(source_run_id, "source-run-id")
    except ValueError as exc:
        raise ReplayContractError("invalid replay source run id") from exc
    artifacts_root = artifacts_root.resolve()
    lexical_runs_root = artifacts_root / "runs"
    lexical_run_root = lexical_runs_root / source_run_id
    if lexical_runs_root.is_symlink():
        raise ReplayContractError("replay runs root must not be a symbolic link or alias")
    if lexical_run_root.is_symlink():
        raise ReplayContractError("replay source run root must not be a symbolic link or alias")
    try:
        runs_root = lexical_runs_root.resolve(strict=True)
        run_root = lexical_run_root.resolve(strict=True)
    except OSError as exc:
        raise ReplayContractError("replay source run root is missing") from exc
    if (
        runs_root != lexical_runs_root
        or run_root != lexical_run_root
        or run_root.parent != runs_root
        or run_root.name != source_run_id
    ):
        raise ReplayContractError("replay source run root must be a direct, non-aliased runs child matching its id")
    required = (
        "run-manifest.json", "hashes.json", "events.jsonl", "run-result.json", "quality-report.json",
        "candidate-draws.jsonl", "observations.jsonl", "reconciliation.jsonl",
    )
    if not run_root.is_dir() or any(not (run_root / name).is_file() for name in required):
        raise ReplayContractError("replay source run is incomplete")
    manifest, manifest_identity = _load_stable_json(run_root / "run-manifest.json")
    result, result_identity = _load_stable_json(run_root / "run-result.json")
    if manifest.get("run_id") != source_run_id or result.get("run_id") != source_run_id:
        raise ReplayContractError("replay source run identity mismatch")
    events = _load_jsonl(run_root / "events.jsonl")
    profile = _replay_profile(manifest)
    terminal = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    if sum(row.get("event_type") in terminal for row in events) != 1:
        raise ReplayContractError("replay source run must have exactly one terminal event")

    hashes, hashes_identity = _load_stable_json(run_root / "hashes.json")
    entries = hashes.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ReplayContractError("replay source hash manifest is empty")
    hashed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in hashed:
            raise ReplayContractError("invalid or duplicate replay hash path")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or "\\" in relative or ".." in pure.parts:
            raise ReplayContractError("unsafe replay hash path")
        hashed[relative] = dict(entry)

    requests = manifest.get("request_plan")
    if not isinstance(requests, list) or not requests:
        raise ReplayContractError("replay source request plan is empty")
    if profile == "snapshot-v1":
        request_inputs = [(request, request.get("input_ref")) for request in requests]
    else:
        request_inputs = _live_replay_requests(manifest, events, result, profile)
    replay_requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_local_files = set(required)
    for request, raw_ref in request_inputs:
        pure, raw_path = _safe_run_local_path(run_root, raw_ref, prefix="raw", label="raw")
        manifest_ref = raw_path.relative_to(artifacts_root).as_posix()
        if raw_ref in seen or not raw_path.is_file() or manifest_ref not in hashed:
            raise ReplayContractError(f"unhashed or duplicate replay raw input: {raw_ref}")
        seen.add(raw_ref)
        expected_local_files.add(pure.as_posix())
        replay_requests.append({
            **request,
            "method": "SNAPSHOT",
            "input_ref": raw_ref,
            "source_raw_path": raw_path,
            "source_raw_sha256": hashed[manifest_ref].get("sha256"),
        })

    configs = manifest.get("config_files")
    if not isinstance(configs, list) or not configs:
        raise ReplayContractError("replay source manifest has no run-local configs")
    replay_configs: list[dict[str, Any]] = []
    config_refs: set[str] = set()
    config_names: set[str] = set()
    for config in configs:
        if not isinstance(config, dict):
            raise ReplayContractError("invalid replay source config entry")
        pure, path = _safe_run_local_path(run_root, config.get("ref"), prefix="config", label="config")
        ref = pure.as_posix()
        name = pure.name
        if ref in config_refs or name in config_names:
            raise ReplayContractError("duplicate run-local replay config ref or basename")
        config_refs.add(ref)
        config_names.add(name)
        manifest_ref = path.relative_to(artifacts_root).as_posix()
        expected_sha = config.get("sha256")
        if manifest_ref not in hashed or not isinstance(expected_sha, str) or sha256_file(path) != expected_sha:
            raise ReplayContractError(f"run-local replay config hash mismatch: {ref}")
        expected_local_files.add(ref)
        replay_configs.append({"ref": ref, "sha256": expected_sha, "source_path": path})

    expected_hashed = {
        (run_root / relative).relative_to(artifacts_root).as_posix()
        for relative in expected_local_files if relative != "hashes.json"
    }
    if set(hashed) != expected_hashed:
        raise ReplayContractError("replay source hash manifest does not exactly cover the replay input closure")
    for relative, entry in hashed.items():
        pure = PurePosixPath(relative)
        path = artifacts_root.joinpath(*pure.parts)
        try:
            local = path.relative_to(run_root).as_posix()
        except ValueError as exc:
            raise ReplayContractError(f"replay hash entry is outside source run: {relative}") from exc
        _, safe_path = _safe_run_local_path(run_root, local, prefix=None, label="hashed input")
        if (
            isinstance(entry.get("size_bytes"), bool)
            or not isinstance(entry.get("size_bytes"), int)
            or safe_path.stat().st_size != entry.get("size_bytes")
            or sha256_file(safe_path) != entry.get("sha256")
        ):
            raise ReplayContractError(f"replay source hash mismatch: {relative}")

    source_inventory = _source_run_inventory(run_root)
    if {item[0] for item in source_inventory} != _expected_inventory_paths(expected_local_files):
        raise ReplayContractError("replay source run inventory contains missing or unexpected paths")
    inventory_by_path = {item[0]: item for item in source_inventory}
    for relative, identity in {
        "run-manifest.json": manifest_identity,
        "run-result.json": result_identity,
        "hashes.json": hashes_identity,
    }.items():
        item = inventory_by_path[relative]
        if (item[2], item[3]) != identity:
            raise ReplayContractError(f"replay source changed while preparing: {relative}")
    for relative, entry in hashed.items():
        local = (artifacts_root / PurePosixPath(relative)).relative_to(run_root).as_posix()
        item = inventory_by_path[local]
        if (item[2], item[3]) != (entry["size_bytes"], entry["sha256"]):
            raise ReplayContractError(f"replay source changed while preparing: {local}")
    return ReplayPlan(
        source_run_id, run_root, tuple(replay_requests), tuple(replay_configs), source_inventory,
    )


def compare_deterministic_outputs(
    source_run_root: Path,
    replay_run_root: Path,
    *,
    artifact_names: Iterable[str] = ("observations.jsonl", "reconciliation.jsonl", "candidate-draws.jsonl"),
) -> dict[str, str]:
    compared: dict[str, str] = {}
    for name in artifact_names:
        source, replay = source_run_root / name, replay_run_root / name
        if not source.is_file() or not replay.is_file() or source.read_bytes() != replay.read_bytes():
            raise ReplayDeterminismError(f"replay deterministic artifact differs: {name}")
        compared[name] = sha256_file(source)
    source_quality = load_json(source_run_root / "quality-report.json").get("deterministic")
    replay_quality = load_json(replay_run_root / "quality-report.json").get("deterministic")
    if source_quality is None or canonical_json_bytes(source_quality) != canonical_json_bytes(replay_quality):
        raise ReplayDeterminismError("replay deterministic quality projection differs")
    return compared


class ReplayReadOnlyGuard:
    """Prove replay did not alter its source closure or publication surfaces."""

    def __init__(
        self,
        artifacts_root: Path,
        *,
        source_run_root: Path | None = None,
        source_inventory: tuple[tuple[str, str, int, str], ...] | None = None,
    ) -> None:
        self.pointer = artifacts_root / "current-release.json"
        self.artifacts_root = artifacts_root
        self.before: bytes | None = None
        self.release_before: tuple[tuple[str, str], ...] = ()
        self.projection_before: tuple[tuple[str, str], ...] = ()
        self.source_run_root = source_run_root
        self.source_before = source_inventory

    @staticmethod
    def _inventory(root: Path) -> tuple[tuple[str, str], ...]:
        if not root.is_dir():
            return ()
        values: list[tuple[str, str]] = []
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            values.append((relative, "dir" if path.is_dir() else sha256_file(path)))
        return tuple(values)

    def _projection_inventory(self) -> tuple[tuple[str, str], ...]:
        ignored = {"runs", "releases", ".publication-journals"}
        values: list[tuple[str, str]] = []
        if not self.artifacts_root.is_dir():
            return ()
        for path in sorted(self.artifacts_root.iterdir(), key=lambda item: item.name):
            if not path.is_dir() or path.name in ignored or path.name.startswith("."):
                continue
            values.append((path.name, repr(self._inventory(path))))
        return tuple(values)

    def __enter__(self) -> "ReplayReadOnlyGuard":
        self.before = self.pointer.read_bytes() if self.pointer.is_file() else None
        self.release_before = self._inventory(self.artifacts_root / "releases")
        self.projection_before = self._projection_inventory()
        if self.source_run_root is not None:
            actual = _source_run_inventory(self.source_run_root)
            if self.source_before is not None and actual != self.source_before:
                raise ReplayMutationError("replay source run changed before execution; recovery required")
            self.source_before = actual
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        mutations: list[str] = []
        try:
            after = self.pointer.read_bytes() if self.pointer.is_file() else None
            if after != self.before:
                mutations.append("current-release.json")
            if self._inventory(self.artifacts_root / "releases") != self.release_before:
                mutations.append("releases inventory")
            if self._projection_inventory() != self.projection_before:
                mutations.append("root projection inventory")
            if self.source_run_root is not None and _source_run_inventory(self.source_run_root) != self.source_before:
                mutations.append("source run inventory")
        except BaseException as inventory_error:
            # A publication surface that changes while it is being measured is
            # itself a failed read-only proof.  Preserve the replay body's
            # exception when one already exists; otherwise retain the inventory
            # failure as the cause.
            cause = exc if isinstance(exc, BaseException) else inventory_error
            error = ReplayMutationError(
                "replay publication state could not be verified; recovery required",
                original_error=cause,
            )
            raise error from cause
        if mutations:
            message = "replay mutated publication state; recovery required: " + ", ".join(mutations)
            error = ReplayMutationError(message, original_error=exc if isinstance(exc, BaseException) else None)
            if exc is not None:
                raise error from exc
            raise error


@contextmanager
def replay_session(artifacts_root: Path, source_run_id: str) -> Iterator[ReplaySession]:
    """Open an offline, non-publishing replay under mutation detection."""
    plan = prepare_replay(artifacts_root, source_run_id)
    with ReplayReadOnlyGuard(
        artifacts_root, source_run_root=plan.source_run_root, source_inventory=plan.source_inventory,
    ):
        yield ReplaySession(plan, OfflineReplayTransport(), DisabledReplayPublication())
