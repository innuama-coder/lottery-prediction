from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ROLES = {
    "acceptance_engineer",
    "contract_owner",
    "data_custodian",
    "implementation_author",
    "independent_oracle_author",
    "independent_power_operator",
    "release_controller",
    "statistical_owner",
    "machine_delivery_statement",
    "run_operator",
    "vps_operator",
    "independent_replay_operator",
    "independent_reviewer",
    "acceptance_approver",
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha256(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def safe_relative(raw: str) -> Path:
    posix = PurePosixPath(raw)
    if posix.is_absolute() or ".." in posix.parts or "latest" in raw or "*" in raw:
        raise ValueError(f"unsafe or mutable path: {raw}")
    path = (ROOT / Path(*posix.parts)).resolve()
    path.relative_to(ROOT.resolve())
    return path


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def require_actor(assignments: dict[str, object], role: str) -> dict[str, object]:
    rows = assignments.get("assignments")
    if not isinstance(rows, list):
        raise ValueError("actor assignments missing assignments array")
    matches = [row for row in rows if isinstance(row, dict) and role in row.get("roles", [])]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one assigned actor for {role}, got {len(matches)}")
    return matches[0]


def verify_actor_contract(assignments: dict[str, object], commit: str) -> dict[str, object]:
    roles = {role for row in assignments.get("assignments", []) if isinstance(row, dict) for role in row.get("roles", [])}
    missing = sorted(EXPECTED_ROLES - roles)
    if missing:
        raise ValueError(f"missing preparation actors: {missing}")
    actors: dict[str, dict[str, object]] = {}
    for row in assignments["assignments"]:
        actor_id = row.get("actor_id")
        session_id = row.get("session_id")
        if not isinstance(actor_id, str) or not actor_id or not isinstance(session_id, str) or not session_id:
            raise ValueError("every actor needs stable actor_id and session_id")
        if actor_id in actors:
            raise ValueError(f"actor_id appears in multiple assignment rows: {actor_id}")
        actors[actor_id] = row
        record_path = safe_relative(str(row.get("task_record_path")))
        if file_sha(record_path) != row.get("task_record_sha256"):
            raise ValueError(f"task record hash mismatch for {actor_id}")
    delivery = require_actor(assignments, "machine_delivery_statement")
    if delivery.get("actor_type") != "codex_session":
        raise ValueError("machine delivery actor must be a codex session")
    product_actor = require_actor(assignments, "implementation_author")["actor_id"]
    power_actor = require_actor(assignments, "independent_power_operator")["actor_id"]
    oracle_actor = require_actor(assignments, "independent_oracle_author")["actor_id"]
    statistical_actor = require_actor(assignments, "statistical_owner")["actor_id"]
    acceptance_actor = require_actor(assignments, "acceptance_engineer")["actor_id"]
    controller_actor = require_actor(assignments, "release_controller")["actor_id"]
    data_actor = require_actor(assignments, "data_custodian")["actor_id"]
    checks = {
        "delivery_statement_is_machine": delivery["actor_id"] in {actor for actor, row in actors.items() if row.get("actor_type") == "codex_session"},
        "power_not_product": power_actor != product_actor,
        "power_not_oracle": power_actor != oracle_actor,
        "power_not_statistical": power_actor != statistical_actor,
        "acceptance_not_product": acceptance_actor != product_actor,
        "t00_acceptor_not_t00_producer": data_actor != controller_actor,
    }
    if not all(checks.values()):
        raise ValueError(f"actor inequality failed: {checks}")
    return {"checks": checks, "status": "PASS"}


def authority_inventory(commit: str, config: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in config["authority_files"]:
        path = str(item["path"])
        content = git("show", f"{commit}:{path}").stdout
        digest = sha256(content)
        if digest != item["sha256"]:
            raise ValueError(f"authority content hash mismatch: {path}")
        blob = git("rev-parse", f"{commit}:{path}").stdout.decode().strip()
        rows.append({"path": path, "git_blob_id": blob, "bytes": len(content), "sha256": digest})
    return rows


def protected_inventory(commit: str, roots: list[str], provenance: dict[str, object]) -> dict[str, object]:
    command = ["ls-tree", "-r", "-t", "-z", "--full-tree", commit, "--", *roots]
    raw = git(*command).stdout
    git_rows: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, path_b = record.split(b"\t", 1)
        mode_b, type_b, object_b = header.split(b" ", 2)
        path = path_b.decode("utf-8", "surrogateescape")
        if any(path == root or path.startswith(f"{root}/") for root in roots):
            git_rows[path] = (type_b.decode(), object_b.decode())
    if not git_rows:
        raise ValueError("protected Git inventory is empty")
    entries: list[dict[str, object]] = []
    children_by_parent: dict[str, list[str]] = {}
    for path in git_rows:
        parent = PurePosixPath(path).parent.as_posix()
        children_by_parent.setdefault(parent, []).append(path)
    actual_paths: set[str] = set()
    for root in roots:
        root_path = safe_relative(root)
        if not root_path.is_dir():
            raise ValueError(f"protected root missing: {root}")
        actual_paths.add(root)
        for current, dirs, files in os.walk(root_path, followlinks=False):
            current_path = Path(current)
            rel_current = current_path.relative_to(ROOT).as_posix()
            actual_paths.add(rel_current)
            for name in dirs + files:
                actual_paths.add((current_path / name).relative_to(ROOT).as_posix())
    expected_paths = set(git_rows)
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)[:20]
        extra = sorted(actual_paths - expected_paths)[:20]
        raise ValueError(f"protected path set mismatch missing={missing} extra={extra}")
    for path in sorted(git_rows):
        git_type, object_id = git_rows[path]
        full = safe_relative(path)
        if git_type == "tree":
            children = sorted(children_by_parent.get(path, []))
            content = canonical(children)
            item_type = "tree"
        elif full.is_symlink():
            content = os.readlink(full).encode("utf-8", "surrogateescape")
            item_type = "symlink"
        else:
            content = full.read_bytes()
            item_type = "file"
        entries.append({"path": path, "type": item_type, "bytes": len(content), "sha256": sha256(content), "git_object_id": object_id})
    diff = git("diff", "--no-ext-diff", "--quiet", commit, "--", *roots, check=False)
    if diff.returncode != 0:
        raise ValueError("protected working tree differs from authority commit")
    inventory_basis = [{key: row[key] for key in ("path", "type", "bytes", "sha256", "git_object_id")} for row in entries]
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_protected_artifact_inventory",
        "authority_commit": commit,
        "generated_at_utc": utc_now(),
        "roots": roots,
        "entries": entries,
        "entry_count": len(entries),
        "file_count": sum(row["type"] != "tree" for row in entries),
        "total_file_bytes": sum(int(row["bytes"]) for row in entries if row["type"] != "tree"),
        "inventory_sha256": sha256(canonical(inventory_basis)),
        "provenance": provenance,
    }


def close_t00_deliverables(output: Path, assignments_path: Path) -> int:
    receipt_path = output / "receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError("A03 core receipt is required before deliverable closure")
    failure_path = output / "attempts/A03/closure-failure.json"
    closure_path = output / "attempts/A04/receipt.json"
    if failure_path.exists() or closure_path.exists():
        raise FileExistsError("immutable T00 A03/A04 closure path already exists")
    failure_path.parent.mkdir(parents=True, exist_ok=False)
    failure = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_failed_attempt",
        "task_id": "T00",
        "attempt_id": "A03",
        "status": "HOLD",
        "terminal": "HOLD_SCHEMA_DELIVERABLE_CLOSURE",
        "process_exit_code": 20,
        "accepted_core_receipt_sha256": file_sha(receipt_path),
        "finding": "A03 froze the authority/genesis/protected evidence correctly, but its receipt did not hash the three T00 schemas or freeze_authority.py. Two schemas were made offline-self-contained after the A03 receipt, so an append-only deliverable closure is required.",
        "protected_or_authority_writes": 0,
        "recovery": {"attempt_id": "A04", "fixed_inputs_unchanged": True, "earliest_recovery_point": "T00_DELIVERABLE_HASH_CLOSURE"},
        "provenance": {
            "producer_actor_id": "p4-release-controller-orchestrator-i01",
            "role": "release_controller",
            "session_id": "/root",
            "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
            "task_id": "T00",
        },
    }
    failure_path.write_bytes(canonical(failure))
    deliverable_paths = [
        ROOT / "config/phase4/authority-freeze.json",
        ROOT / "config/phase4/genesis.json",
        ROOT / "schemas/phase4/authority-freeze.schema.json",
        ROOT / "schemas/phase4/genesis.schema.json",
        ROOT / "schemas/phase4/protected-inventory.schema.json",
        ROOT / "scripts/phase4/freeze_authority.py",
    ]
    deliverable_paths.extend(sorted((output.parent.parent / "control").rglob("*.json")))
    deliverable_paths.extend(sorted(path for path in output.rglob("*.json") if "A04" not in path.parts))
    relative_seen: set[str] = set()
    inputs: list[dict[str, object]] = []
    for path in deliverable_paths:
        relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
        if relative in relative_seen:
            continue
        relative_seen.add(relative)
        inputs.append({"path": relative, "bytes": path.stat().st_size, "sha256": file_sha(path)})
    assignments = load_json(assignments_path)
    controller = require_actor(assignments, "release_controller")
    custodian = require_actor(assignments, "data_custodian")
    closure_path.parent.mkdir(parents=True, exist_ok=False)
    closure = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_work_item_attempt_receipt",
        "task_id": "T00",
        "attempt_id": "A04",
        "identity": "p4-prep-controller-issued-i01",
        "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
        "parent_core_receipt_sha256": file_sha(receipt_path),
        "actor_assignment_sha256": file_sha(assignments_path),
        "task_producer_set": [controller["actor_id"]],
        "acceptance_actor_provenance": {key: custodian[key] for key in ("actor_id", "session_id", "task_record_path", "task_record_sha256")},
        "closed_deliverables": sorted(inputs, key=lambda row: str(row["path"])),
        "command": [sys.executable, str(Path(__file__).relative_to(ROOT)), *sys.argv[1:]],
        "process_exit_code": 0,
        "status": "PASS",
        "terminal": "T00_DELIVERABLE_HASH_CLOSURE",
        "protected_or_authority_writes": 0,
    }
    closure_path.write_bytes(canonical(closure))
    print(json.dumps({"status": "PASS", "task_id": "T00", "attempt_id": "A04", "receipt": closure_path.relative_to(ROOT).as_posix(), "sha256": file_sha(closure_path)}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prep-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--phase3-release", required=True)
    parser.add_argument("--protected-root", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    parser.add_argument("--close-deliverables", action="store_true")
    args = parser.parse_args()
    started = utc_now()
    authority = load_json(ROOT / "config/phase4/authority-freeze.json")
    expected_prep = next(row["id"] for row in authority["namespace_contract"] if row["kind"] == "preparation")
    if args.prep_id != expected_prep:
        raise ValueError("unexpected controller-issued preparation identity")
    if args.phase3_release != "P3-R07-2c0fa97-20260810-I01":
        raise ValueError("Phase 3 release identity mismatch")
    output = args.output.resolve()
    output.relative_to(ROOT.resolve())
    if args.close_deliverables:
        return close_t00_deliverables(output, args.actor_assignments.resolve())
    if output.exists():
        existing = {path.name for path in output.iterdir()}
        preserved = all((output / f"attempts/{attempt}/failure.json").is_file() for attempt in ("A01", "A02"))
        if existing != {"attempts"} or not preserved:
            raise FileExistsError(f"immutable T00 output already exists: {output}")
    genesis = load_json(ROOT / "config/phase4/genesis.json")
    assignments = load_json(args.actor_assignments.resolve())
    if args.commit != authority["authority_commit"]:
        raise ValueError("authority commit argument mismatch")
    if git("merge-base", "--is-ancestor", authority["required_ancestor_commit"], args.commit, check=False).returncode != 0:
        raise ValueError("required authority commit is not an ancestor of selected authority")
    if args.protected_root != authority["protected_roots"]:
        raise ValueError("protected roots must be exact and ordered")
    if git("merge-base", "--is-ancestor", args.commit, "origin/main", check=False).returncode != 0:
        raise ValueError("authority commit is not an ancestor of origin/main")
    if git("merge-base", "--is-ancestor", args.commit, "origin/main", check=False).returncode != 0:
        raise ValueError("T00 requires the frozen authority commit to be an origin/main ancestor")
    role_audit = verify_actor_contract(assignments, args.commit)
    provenance = dict(authority["provenance"])
    authorities = authority_inventory(args.commit, authority)
    phase3 = authority["phase3_acceptance"]
    if file_sha(safe_relative(str(phase3["path"]))) != phase3["sha256"]:
        raise ValueError("Phase 3 acceptance hash mismatch")
    genesis_checks: list[dict[str, object]] = []
    for kind in ("manifest", "records", "observations"):
        path = safe_relative(str(genesis[f"base_phase1_{kind}_path"]))
        expected = genesis[f"base_phase1_{kind}_sha256"]
        actual = file_sha(path)
        genesis_checks.append({"kind": kind, "path": path.relative_to(ROOT).as_posix(), "expected_sha256": expected, "actual_sha256": actual, "status": "PASS" if actual == expected else "FAIL"})
    if any(row["status"] != "PASS" for row in genesis_checks):
        raise ValueError("Phase 1 genesis content mismatch")
    paths = [safe_relative(str(row["path"])) for row in authority["namespace_contract"]]
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError("Phase 4 namespaces are nested or reused")
    output.mkdir(parents=True, exist_ok=True)
    inventory = protected_inventory(args.commit, args.protected_root, provenance)
    artifacts = {
        "authority-inventory.json": {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_authority_git_inventory",
            "authority_commit": args.commit,
            "origin_main": git("rev-parse", "origin/main").stdout.decode().strip(),
            "files": authorities,
            "provenance": provenance,
        },
        "genesis-verification.json": {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_genesis_verification",
            "genesis": genesis,
            "checks": genesis_checks,
            "status": "PASS",
            "provenance": provenance,
        },
        "namespace-verification.json": {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_namespace_verification",
            "namespaces": authority["namespace_contract"],
            "realpaths": [str(path) for path in paths],
            "mutually_non_ancestral": True,
            "ids_unique": len({row["id"] for row in authority["namespace_contract"]}) == 4,
            "status": "PASS",
            "provenance": provenance,
        },
        "protected-artifact-inventory.json": inventory,
        "role-audit.json": {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_actor_inequality_audit",
            "actor_assignments_sha256": file_sha(args.actor_assignments.resolve()),
            **role_audit,
            "provenance": provenance,
        },
    }
    for name, payload in artifacts.items():
        (output / name).write_bytes(canonical(payload))
    output_rows = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": file_sha(path), "bytes": path.stat().st_size,
         "producer_actor_id": require_actor(assignments, "release_controller")["actor_id"], "task_id": "T00",
         "session_id": require_actor(assignments, "release_controller")["session_id"], "source_commit": args.commit,
         "role": "release_controller"}
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    receipt = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_work_item_receipt",
        "task_id": "T00",
        "identity": args.prep_id,
        "source_commit": args.commit,
        "actor_assignment_sha256": file_sha(args.actor_assignments.resolve()),
        "task_producer_set": [require_actor(assignments, "release_controller")["actor_id"]],
        "acceptance_actor_provenance": {
            key: require_actor(assignments, "data_custodian")[key]
            for key in ("actor_id", "session_id", "task_record_path", "task_record_sha256")
        },
        "inputs": [
            {"path": "config/phase4/authority-freeze.json", "sha256": file_sha(ROOT / "config/phase4/authority-freeze.json")},
            {"path": "config/phase4/genesis.json", "sha256": file_sha(ROOT / "config/phase4/genesis.json")},
            {"path": args.actor_assignments.resolve().relative_to(ROOT).as_posix(), "sha256": file_sha(args.actor_assignments.resolve())},
        ],
        "outputs": output_rows,
        "command": [sys.executable, str(Path(__file__).relative_to(ROOT)), *sys.argv[1:]],
        "started_at_utc": started,
        "ended_at_utc": utc_now(),
        "process_exit_code": 0,
        "status": "PASS",
        "terminal": "T00_AUTHORITY_GENESIS_PROTECTION_FROZEN",
        "role_inequalities": role_audit["checks"],
    }
    (output / "receipt.json").write_bytes(canonical(receipt))
    print(json.dumps({"status": "PASS", "task_id": "T00", "output": output.relative_to(ROOT).as_posix(), "inventory_sha256": inventory["inventory_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
