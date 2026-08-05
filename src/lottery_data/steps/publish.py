from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from lottery_data.artifacts import atomic_write_bytes, atomic_write_json, atomic_write_jsonl, load_json, validate_stable_id
from lottery_data.models import validate_object
from lottery_data.serialization import canonical_json_bytes, sha256_file
from lottery_data.steps.locking import LockUnavailable, OSFileLock
from lottery_data.steps.publication_journal import PublicationJournal, tree_sha256


class PublishError(RuntimeError):
    pass


class RollbackPreconditionError(PublishError):
    pass


class PublishDestinationExistsError(PublishError):
    pass


@dataclass(frozen=True)
class PublicationToken:
    artifacts_root: Path
    run_root: Path
    run_id: str
    release_id: str
    projection_root: Path
    original_pointer_bytes: bytes | None
    committed_pointer_bytes: bytes
    journal: PublicationJournal


@contextmanager
def no_change_guard(artifacts_root: Path, run_id: str, expected_pointer_bytes: bytes):
    """Hold the writer lock while proving a no-change decision against the frozen pointer."""
    try:
        validate_stable_id(run_id, "run-id")
    except ValueError as exc:
        raise PublishError(str(exc)) from exc
    with PublishLock(artifacts_root / ".publish.lock", run_id):
        pointer = artifacts_root / "current-release.json"
        actual = pointer.read_bytes() if pointer.is_file() else None
        if actual != expected_pointer_bytes:
            raise PublishError("current-release compare-and-swap precondition failed")
        yield


class PublishLock:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._lock: OSFileLock | None = None

    def __enter__(self) -> "PublishLock":
        try:
            self._lock = OSFileLock(self.path).acquire()
        except LockUnavailable as exc:
            raise PublishError("publish lock is held") from exc
        try:
            # A non-NUL file is a lock created by the previous existence-based
            # implementation. Read it through the descriptor that owns the OS
            # lock: opening a second handle is denied by Windows byte locking.
            if self._lock.read_locked_bytes() != b"\0":
                raise PublishError("legacy publish lock is held")
        except Exception:
            self._lock.release()
            self._lock = None
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None


def _read_current_release(artifacts_root: Path) -> str | None:
    pointer = artifacts_root / "current-release.json"
    if not pointer.exists():
        return None
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
        release_id = value["release_id"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PublishError("current-release.json is invalid") from exc
    if not isinstance(release_id, str):
        raise PublishError("current release_id is invalid")
    return release_id


def _hash_entries(root: Path, roles: Mapping[str, str], generated_at_utc: str) -> dict[str, Any]:
    entries = []
    for relative in sorted(roles):
        path = root / relative
        entries.append({
            "path": relative,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "role": roles[relative],
        })
    return {
        "hash_manifest_schema_version": "1.0.0",
        "hash_profile": "sha256-file-manifest-v1",
        "generated_at_utc": generated_at_utc,
        "entries": entries,
    }


def _verify_release(root: Path) -> dict[str, Any]:
    manifest = load_json(root / "manifest.json")
    validate_object("DatasetRelease", manifest)
    draws = [json.loads(line) for line in (root / "draws.jsonl").read_text(encoding="utf-8").splitlines() if line]
    observations = [json.loads(line) for line in (root / "observations.jsonl").read_text(encoding="utf-8").splitlines() if line]
    for value in draws:
        validate_object("DrawRecord", value)
    for value in observations:
        validate_object("SourceObservation", value)
    counts = Counter(value["game"] for value in draws)
    normalized_counts = {"ssq": counts["ssq"], "dlt": counts["dlt"]}
    if normalized_counts != manifest["record_count_by_game"] or len(observations) != manifest["observation_count"]:
        raise PublishError("release counts do not match manifest")
    if sha256_file(root / "draws.jsonl") != manifest["records_sha256"]:
        raise PublishError("draws hash does not match manifest")
    if sha256_file(root / "observations.jsonl") != manifest["observations_sha256"]:
        raise PublishError("observations hash does not match manifest")
    if load_json(root / "quality-report.json").get("decision") != "PASS":
        raise PublishError("quality report is not PASS")
    hashes = load_json(root / "hashes.json")
    for entry in hashes.get("entries", []):
        path = root / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["size_bytes"] or sha256_file(path) != entry["sha256"]:
            raise PublishError(f"release hash mismatch: {entry.get('path')}")
    return manifest


def publish_release(
    *,
    artifacts_root: Path,
    run_root: Path,
    run_id: str,
    release_id: str,
    previous_release_id: str | None,
    manifest_sha256: str,
    schema_bundle_sha256: str,
    pipeline_bundle_sha256: str,
    draws: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    quality_report_path: Path,
    created_at_utc: str,
) -> PublicationToken:
    try:
        validate_stable_id(run_id, "run-id")
        validate_stable_id(release_id, "release-id")
        if previous_release_id is not None:
            validate_stable_id(previous_release_id, "previous-release-id")
    except ValueError as exc:
        raise PublishError(str(exc)) from exc
    releases = artifacts_root / "releases"
    final = releases / release_id
    temporary = releases / f".{release_id}.tmp-{run_id}"
    projection = artifacts_root / release_id
    projection_temporary = artifacts_root / f".{release_id}.tmp-{run_id}"
    recovery = run_root / "rejected-release"
    projection_recovery = run_root / "rejected-projection"
    with PublishLock(artifacts_root / ".publish.lock", run_id):
        pointer_path = artifacts_root / "current-release.json"
        original_pointer_bytes = pointer_path.read_bytes() if pointer_path.is_file() else None
        if _read_current_release(artifacts_root) != previous_release_id:
            raise PublishError("current-release compare-and-swap precondition failed")
        if final.exists() or temporary.exists() or projection.exists() or projection_temporary.exists():
            raise PublishDestinationExistsError("release destination or root projection already exists")
        releases.mkdir(parents=True, exist_ok=True)
        temporary.mkdir()
        journal: PublicationJournal | None = None
        quarantined: list[str] = []
        try:
            atomic_write_jsonl(temporary / "draws.jsonl", draws, sort_keys=("game", "issue_id", "revision_id"))
            atomic_write_jsonl(temporary / "observations.jsonl", observations, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))
            shutil.copyfile(quality_report_path, temporary / "quality-report.json")
            counts = Counter(row["game"] for row in draws)
            release_manifest = {
                "release_schema_version": "1.0.0",
                "release_id": release_id,
                "created_at_utc": created_at_utc,
                "previous_release_id": previous_release_id,
                "input_run_id": run_id,
                "record_count_by_game": {"ssq": counts["ssq"], "dlt": counts["dlt"]},
                "observation_count": len(observations),
                "input_manifest_sha256": manifest_sha256,
                "schema_bundle_sha256": schema_bundle_sha256,
                "pipeline_bundle_sha256": pipeline_bundle_sha256,
                "records_sha256": sha256_file(temporary / "draws.jsonl"),
                "observations_sha256": sha256_file(temporary / "observations.jsonl"),
                "quality_report_ref": f"releases/{release_id}/quality-report.json",
                "status": "published",
            }
            validate_object("DatasetRelease", release_manifest)
            atomic_write_json(temporary / "manifest.json", release_manifest)
            roles = {"draws.jsonl": "draw", "observations.jsonl": "observation", "manifest.json": "manifest", "quality-report.json": "quality"}
            atomic_write_json(temporary / "hashes.json", _hash_entries(temporary, roles, created_at_utc))
            _verify_release(temporary)
            projection_temporary.mkdir()
            for name in ("draws.jsonl", "observations.jsonl", "manifest.json", "quality-report.json", "hashes.json"):
                shutil.copyfile(temporary / name, projection_temporary / name)
            _verify_release(projection_temporary)
            for name in ("draws.jsonl", "observations.jsonl", "manifest.json", "quality-report.json", "hashes.json"):
                if (temporary / name).read_bytes() != (projection_temporary / name).read_bytes():
                    raise PublishError(f"root projection differs from release: {name}")
            pointer = {
                "pointer_schema_version": "1.0.0",
                "release_id": release_id,
                "manifest_ref": f"releases/{release_id}/manifest.json",
                "manifest_sha256": sha256_file(temporary / "manifest.json"),
                "updated_at_utc": created_at_utc,
                "updated_by_run_id": run_id,
            }
            committed_pointer_bytes = canonical_json_bytes(pointer)
            journal = PublicationJournal.create(
                artifacts_root=artifacts_root,
                run_id=run_id,
                release_id=release_id,
                original_pointer_bytes=original_pointer_bytes,
                committed_pointer_bytes=committed_pointer_bytes,
                release_path=final.relative_to(artifacts_root).as_posix(),
                projection_path=projection.relative_to(artifacts_root).as_posix(),
                release_tree_sha256=tree_sha256(temporary),
                projection_tree_sha256=tree_sha256(projection_temporary),
                temporary_paths=[
                    temporary.relative_to(artifacts_root).as_posix(),
                    projection_temporary.relative_to(artifacts_root).as_posix(),
                ],
                updated_at_utc=created_at_utc,
            )
            os.replace(temporary, final)
            journal.advance("RELEASE_RENAMED", updated_at_utc=created_at_utc)
            os.replace(projection_temporary, projection)
            journal.advance("PROJECTION_RENAMED", updated_at_utc=created_at_utc)
            atomic_write_bytes(pointer_path, committed_pointer_bytes)
            journal.advance("POINTER_COMMITTED", updated_at_utc=created_at_utc)
            return PublicationToken(
                artifacts_root=artifacts_root,
                run_root=run_root,
                run_id=run_id,
                release_id=release_id,
                projection_root=projection,
                original_pointer_bytes=original_pointer_bytes,
                committed_pointer_bytes=committed_pointer_bytes,
                journal=journal,
            )
        except Exception:
            if projection_temporary.exists():
                if projection_recovery.exists():
                    shutil.rmtree(projection_temporary)
                else:
                    os.replace(projection_temporary, projection_recovery)
                    quarantined.append(projection_recovery.relative_to(artifacts_root).as_posix())
            elif projection.exists() and _read_current_release(artifacts_root) == previous_release_id:
                if projection_recovery.exists():
                    shutil.rmtree(projection)
                else:
                    os.replace(projection, projection_recovery)
                    quarantined.append(projection_recovery.relative_to(artifacts_root).as_posix())
            if temporary.exists():
                if recovery.exists():
                    shutil.rmtree(temporary)
                else:
                    os.replace(temporary, recovery)
                    quarantined.append(recovery.relative_to(artifacts_root).as_posix())
            elif final.exists() and _read_current_release(artifacts_root) == previous_release_id:
                if recovery.exists():
                    shutil.rmtree(final)
                else:
                    os.replace(final, recovery)
                    quarantined.append(recovery.relative_to(artifacts_root).as_posix())
            if journal is not None and _read_current_release(artifacts_root) == previous_release_id:
                journal.complete_recovery(
                    updated_at_utc=created_at_utc,
                    quarantined=quarantined,
                    status="interrupted",
                )
            raise


def rollback_publication(token: PublicationToken) -> Path:
    pointer = token.artifacts_root / "current-release.json"
    release = token.artifacts_root / "releases" / token.release_id
    projection = token.projection_root
    recovery = token.run_root / "recovery" / "published-release"
    projection_recovery = token.run_root / "recovery" / "published-projection"
    with PublishLock(token.artifacts_root / ".publish.lock", token.run_id + "-rollback"):
        actual = pointer.read_bytes() if pointer.is_file() else None
        if actual != token.committed_pointer_bytes:
            raise RollbackPreconditionError("current pointer changed after this run published")
        if not release.is_dir() or not projection.is_dir() or recovery.exists() or projection_recovery.exists():
            raise RollbackPreconditionError("published release cannot be uniquely recovered")
        if token.original_pointer_bytes is None:
            pointer.unlink()
        else:
            atomic_write_bytes(pointer, token.original_pointer_bytes)
        os.replace(projection, projection_recovery)
        os.replace(release, recovery)
    return recovery
