from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/phase4/authority-freeze.json"
REQUIRED_PATHS = {
    "ROADMAP.md",
    "tasks/phase4/README.md",
    "docs/research/phase-4-overall-design.md",
    "docs/plans/phase-4-detailed-plan.md",
}
REQUIRED_MARKERS = (
    "serving_model_by_game",
    "M0",
    "retrospective_sequence_safe",
    "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE",
)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fail(message: str) -> int:
    print(json.dumps({"status": "HOLD", "reason_code": "HOLD_AUTHORITY_SYNC", "error": message}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 20


def validate(args: argparse.Namespace) -> dict[str, object]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    commit = str(config["authority_commit"])
    if args.commit and args.commit != commit:
        raise ValueError("--commit does not match frozen authority_commit")
    if git("merge-base", "--is-ancestor", str(config["input_commit"]), commit, check=False).returncode:
        raise ValueError("authority commit is not a descendant of the authorized input commit")
    rows = config["authority_files"]
    if {row["path"] for row in rows} != REQUIRED_PATHS:
        raise ValueError("authority path set is not exact")
    facts = []
    texts: dict[str, str] = {}
    for row in rows:
        path = row["path"]
        content = git("show", f"{commit}:{path}").stdout
        actual = sha256(content)
        if actual != row["sha256"]:
            raise ValueError(f"authority hash mismatch: {path}")
        texts[path] = content.decode("utf-8")
        facts.append({"path": path, "sha256": actual, "bytes": len(content)})
    combined = "\n".join(texts.values())
    for marker in REQUIRED_MARKERS:
        if marker not in combined:
            raise ValueError(f"required semantic marker missing: {marker}")
    semantics = config["required_semantics"]
    if args.require_serving_model_per_game and not semantics["serving_model_per_game"]:
        raise ValueError("serving model per game is not required")
    if args.reject_baseline_only_pass and semantics["baseline_only_product_pass_allowed"]:
        raise ValueError("baseline_only can still form product PASS")
    if not semantics["serving_must_be_non_m0"] or not semantics["serving_must_be_non_uniform"]:
        raise ValueError("real non-uniform serving hard gates are missing")
    if semantics["final_machine_state"] != "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE":
        raise ValueError("final machine state mismatch")
    if (semantics.get("local_product_verifier") != "P4-LOCAL-STABLE-SCORE-KEY-2"
            or semantics.get("local_supported_python") != "CPython 3.12 any patch"
            or semantics.get("historical_phase2_suites_required_locally") is not False):
        raise ValueError("portable local verifier semantics missing")
    head = git("rev-parse", "HEAD").stdout.decode().strip()
    if git("merge-base", "--is-ancestor", commit, head, check=False).returncode:
        raise ValueError("authority commit is not an ancestor of HEAD")
    if git("diff", "--quiet", commit, "--", *sorted(REQUIRED_PATHS), check=False).returncode:
        raise ValueError("authority documents changed after P4_AUTHORITY_COMMIT")
    return {
        "schema_version": "2.0.0",
        "artifact_type": "phase4_d00_authority_check",
        "task_id": "D00",
        "authority_commit": commit,
        "authority_files": facts,
        "requirement_ids": config["requirement_ids"],
        "required_semantics": semantics,
        "checker": "primary",
        "status": "PASS",
        "blocking_findings": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--require-serving-model-per-game", action="store_true")
    parser.add_argument("--reject-baseline-only-pass", action="store_true")
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args)
        if args.output:
            output = args.output.resolve()
            output.relative_to(ROOT.resolve())
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise FileExistsError(f"create-once output exists: {output}")
            output.write_bytes(canonical(result))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
