from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/phase4/authority-freeze.json"


def git_show(commit: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        commit = config["authority_commit"]
        texts = {}
        hashes = {}
        for row in config["authority_files"]:
            raw = git_show(commit, row["path"])
            hashes[row["path"]] = hashlib.sha256(raw).hexdigest()
            if hashes[row["path"]] != row["sha256"]:
                raise ValueError(f"hash mismatch: {row['path']}")
            texts[row["path"]] = raw.decode()
        joined = "\n".join(texts.values())
        checks = {
            "both_games_named": all(game in joined.lower() for game in ("ssq", "dlt")),
            "non_m0_serving_gate": "serving_model_by_game.ssq != M0" in joined and "serving_model_by_game.dlt != M0" in joined,
            "non_uniform_gate": "Top-1000 全等概率" in joined and "完整空间单一 tie" in joined,
            "sequence_safe_gate": "retrospective_sequence_safe" in joined,
            "report_only_isolation": "report-only" in joined and "selection folds" in joined,
            "final_state": "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE" in joined,
            "d00_before_d01": "D00" in joined and "D01" in joined,
            "requirements_complete": set(re.findall(r"P4-R(?:0[1-9]|1[0-7])", joined)) == set(config["requirement_ids"]),
            "probability_display_v2_formula": "P4-LOCAL-STABLE-SCORE-KEY-3" in joined and "17 / 2^52 = 3.774758283725532e-15" in joined,
            "probability_display_v2_narrow_paths": "top1000_derived_probability_display_v2" in joined and "三个 `joint_probability`" in joined,
            "exact_identity_semantics_retained": "score/tie identity" in joined and "create-once" in joined and "exact" in joined,
            "config_rejects_baseline_pass": config["required_semantics"]["baseline_only_product_pass_allowed"] is False,
            "config_rejects_m0_lock": config["required_semantics"]["m0_product_lock_allowed"] is False,
        }
        if not all(checks.values()):
            raise ValueError(f"semantic checks failed: {checks}")
        result = {"schema_version": "2.0.0", "artifact_type": "phase4_d00_independent_semantic_check", "task_id": "D00", "authority_commit": commit, "authority_hashes": hashes, "checks": checks, "old_m0_product_success_paths": 0, "checker": "independent", "status": "PASS", "blocking_findings": []}
        encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if args.output:
            output = args.output.resolve()
            output.relative_to(ROOT.resolve())
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise FileExistsError(f"create-once output exists: {output}")
            output.write_bytes(encoded)
        print(encoded.decode().strip())
        return 0
    except Exception as exc:
        print(json.dumps({"status": "HOLD", "reason_code": "HOLD_AUTHORITY_SYNC", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
