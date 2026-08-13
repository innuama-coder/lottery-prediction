#!/usr/bin/env python3
"""Checkpointable frozen scientific controller driver for T16."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

GAMES = ("dlt", "ssq")
WORLDS = ("uniform", "static_bias", "slow_drift", "useful_feature")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seed(design_id: str, game: str, world: str, ordinal: int) -> int:
    basis = f"P4-SEED-v2|{design_id}|formal-qualification|{game}|{world}|{ordinal}"
    return int.from_bytes(hashlib.sha256(basis.encode()).digest(), "big")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_formal(release_root: Path, *, sequences: int = 1000, stop_after: int | None = None, resume: bool = False) -> dict[str, Any]:
    release_root = release_root.resolve()
    design = json.loads((release_root / "qualification-design/qualification-design.json").read_text(encoding="utf-8"))
    command = json.loads((release_root / "contracts/power-controller-command.json").read_text(encoding="utf-8"))
    out = release_root / "qualification"
    if out.exists() and not resume:
        raise ValueError("formal qualification identity already exists; use --resume")
    out.mkdir(parents=True, exist_ok=resume)
    identity = command["controller_identity"]
    argv = list(command["argv"])
    if argv[0] in {"python", "python3", "/usr/bin/python3"}:
        argv[0] = sys.executable
    completed_total = 0
    cells: list[dict[str, Any]] = []
    for game in GAMES:
        for world in WORLDS:
            shard = out / f"{game}-{world}-terminals.jsonl"
            terminals = _read_jsonl(shard)
            if len(terminals) > sequences:
                raise ValueError("formal checkpoint contains excess terminals")
            for ordinal, terminal in enumerate(terminals, start=1):
                if terminal.get("sequence_ordinal") != ordinal or terminal.get("game") != game or terminal.get("world") != world:
                    raise ValueError("formal checkpoint identity/order mismatch")
            completed_total += len(terminals)
            if len(terminals) < sequences:
                process = subprocess.Popen(argv + ["--stream"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                assert process.stdin is not None and process.stdout is not None
                try:
                    with shard.open("ab") as handle:
                        for ordinal in range(len(terminals) + 1, sequences + 1):
                            value = seed(design["design_id"], game, world, ordinal)
                            seed_text = str(value)
                            request = {
                                "schema_version": "1.0.0", "artifact_type": "phase4_scientific_controller_request",
                                "request_id": f"formal-{game}-{world}-{ordinal}",
                                "expected_controller_identity_id": identity["controller_identity_id"],
                                "design": design, "game": game, "world": world, "sequence_ordinal": ordinal,
                                "seed_domain": "formal-qualification", "input_mode": "seed",
                                "seed_uint256": seed_text, "seed_commitment_sha256": sha(seed_text.encode()), "raw_draws": None,
                            }
                            process.stdin.write(canonical(request) + b"\n"); process.stdin.flush()
                            line = process.stdout.readline()
                            if not line:
                                raise RuntimeError("formal controller stream stopped")
                            terminal = json.loads(line)["sequence_terminal"]
                            handle.write(canonical(terminal) + b"\n")
                            completed_total += 1
                            if ordinal % 10 == 0:
                                handle.flush(); os.fsync(handle.fileno())
                            if stop_after is not None and completed_total >= stop_after:
                                handle.flush(); os.fsync(handle.fileno())
                                checkpoint = {"artifact_type":"phase4_formal_checkpoint","schema_version":"1.0.0","design_id":design["design_id"],"completed_sequences":completed_total,"last_cell":[game,world],"last_ordinal":ordinal,"status":"HOLD","terminal":"FORMAL_CHECKPOINT_RECORDED"}
                                (out / "checkpoint.json").write_bytes(canonical(checkpoint))
                                return {"status":"HOLD","terminal":"FORMAL_CHECKPOINT_RECORDED","exit_code":20,"completed_sequences":completed_total}
                finally:
                    process.terminate(); process.wait(timeout=30)
            terminals = _read_jsonl(shard)
            success = sum(item.get("sequence_event") is True for item in terminals)
            threshold = "<=50" if world == "uniform" else ">=900"
            gate = success <= 50 if world == "uniform" else success >= 900
            cells.append({"game":game,"world":world,"sequence_count":len(terminals),"success_count":success,"sequence_rate":format(success/sequences,".6f"),"gate_threshold":threshold,"gate_pass":gate,"terminals_path":shard.relative_to(release_root).as_posix(),"terminals_sha256":sha(shard.read_bytes())})
    passed = all(row["sequence_count"] == sequences and row["gate_pass"] for row in cells)
    summary = {"artifact_type":"phase4_formal_qualification_summary","schema_version":"1.0.0","design_id":design["design_id"],"seed_domain":"formal-qualification","sequence_count":sum(row["sequence_count"] for row in cells),"cells":cells,"status":"PASS" if passed else "FAIL","terminal":"FORMAL_QUALIFICATION_PASS" if passed else "FAIL_FORMAL_QUALIFICATION"}
    (out / "summary.json").write_bytes(canonical(summary))
    return {**summary, "exit_code": 0 if passed else 5}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--sequences-per-cell", type=int, default=1000)
    parser.add_argument("--stop-after-sequences", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run_formal(args.release_root, sequences=args.sequences_per_cell, stop_after=args.stop_after_sequences, resume=args.resume)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
