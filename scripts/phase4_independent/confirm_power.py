#!/usr/bin/env python3
"""Run the frozen scientific controller as a black box for T13 power cells."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from decimal import Decimal, localcontext
from math import comb
from pathlib import Path
from typing import Any

GAMES = ("dlt", "ssq")
WORLDS = ("uniform", "static_bias", "slow_drift", "useful_feature")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def seed(design_id: str, domain: str, game: str, world: str, ordinal: int) -> int:
    return int.from_bytes(hashlib.sha256(f"P4-SEED-v2|{design_id}|{domain}|{game}|{world}|{ordinal}".encode()).digest(), "big")


class StreamInvoker:
    def __init__(self, argv: list[str]) -> None:
        command = list(argv)
        if os.name == "nt" and command[0] in {"python3", "/usr/bin/python3"}:
            command[0] = sys.executable
        env = os.environ.copy(); env["PYTHONPATH"] = "src"
        self.process = subprocess.Popen(command + ["--stream"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(canonical(request) + b"\n"); self.process.stdin.flush()
        line = self.process.stdout.readline()
        if self.process.poll() is not None or not line:
            raise RuntimeError("scientific controller stream failed")
        return json.loads(line)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=30)


def _run_cell(
    *,
    design: dict[str, Any],
    identity: dict[str, Any],
    argv: list[str],
    seed_domain: str,
    sequences_per_cell: int,
    game: str,
    world: str,
    output: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Run one cell through its own persistent black-box worker.

    Cell-local streams are independent.  Parallel scheduling therefore cannot
    alter sequence ordinals, seed derivation, terminal ordering, or hashes.
    """
    successes = 0
    commitments: list[str] = []
    terminals: list[dict[str, Any]] = []
    stream = StreamInvoker(argv)
    try:
        for ordinal in range(1, sequences_per_cell + 1):
            value = seed(design["design_id"], seed_domain, game, world, ordinal)
            seed_text = str(value)
            commitment = sha(seed_text.encode("ascii"))
            request = {
                "schema_version": "1.0.0",
                "artifact_type": "phase4_scientific_controller_request",
                "request_id": f"power-{game}-{world}-{ordinal}",
                "expected_controller_identity_id": identity["controller_identity_id"],
                "design": design,
                "game": game,
                "world": world,
                "sequence_ordinal": ordinal,
                "seed_domain": seed_domain,
                "input_mode": "seed",
                "seed_uint256": seed_text,
                "seed_commitment_sha256": commitment,
                "raw_draws": None,
            }
            response = stream.invoke(request)
            terminal = response["sequence_terminal"]
            # sequence_event means that the controller emitted the registered
            # proposal event.  For uniform this is the false-proposal count
            # (bounded above); for positive worlds it is the recovery count
            # (bounded below).  The reducer applies the world-specific gate.
            success = terminal["sequence_event"] is True
            successes += int(success)
            commitments.append(commitment)
            terminals.append(terminal)
    finally:
        stream.close()

    shard = output / f"{game}-{world}-terminals.json"
    shard.write_bytes(canonical(terminals))
    return ({
        "game": game,
        "world": world,
        "sequence_count": sequences_per_cell,
        "success_count": successes,
        "seed_set_sha256": sha(canonical(sorted(commitments))),
        "terminals_path": shard.name,
        "terminals_sha256": sha(shard.read_bytes()),
    }, commitments)


def collect_cells(
    *,
    design: dict[str, Any],
    identity: dict[str, Any],
    argv: list[str],
    seed_domain: str,
    sequences_per_cell: int,
    output: Path,
    cell_workers: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    ordered_keys = [(game, world) for game in GAMES for world in WORLDS]
    workers = min(max(1, cell_workers), len(ordered_keys))
    futures: dict[tuple[str, str], Future[tuple[dict[str, Any], list[str]]]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="phase4-power-cell") as pool:
        for game, world in ordered_keys:
            futures[(game, world)] = pool.submit(
                _run_cell,
                design=design,
                identity=identity,
                argv=argv,
                seed_domain=seed_domain,
                sequences_per_cell=sequences_per_cell,
                game=game,
                world=world,
                output=output,
            )
        # Resolve in canonical game/world order, never completion order.
        results = [futures[key].result() for key in ordered_keys]
    return [item[0] for item in results], [commitment for item in results for commitment in item[1]]


def invoke(argv: list[str], request: dict[str, Any]) -> dict[str, Any]:
    command = list(argv)
    if os.name == "nt" and command[0] in {"python3", "/usr/bin/python3"}:
        command[0] = sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    completed = subprocess.run(command, input=canonical(request), capture_output=True, env=env, check=False)
    if completed.returncode or completed.stderr:
        raise RuntimeError("scientific controller invocation failed")
    return json.loads(completed.stdout)


def _cdf_le(n: int, p: Decimal, k: int) -> Decimal:
    if k > n // 2:
        return Decimal(1) - sum(Decimal(comb(n, j)) * p**j * (1-p)**(n-j) for j in range(k + 1, n + 1))
    return sum(Decimal(comb(n, j)) * p**j * (1-p)**(n-j) for j in range(k + 1))


def _cp_interval(n: int, k: int) -> list[str]:
    tail = Decimal("0.05") / Decimal(16)
    with localcontext() as context:
        context.prec = 80
        if k == 0:
            lower = Decimal(0)
        else:
            lo, hi = Decimal(0), Decimal(1)
            for _ in range(48):
                mid = (lo + hi) / 2
                if 1 - _cdf_le(n, mid, k - 1) < tail: lo = mid
                else: hi = mid
            lower = (lo + hi) / 2
        if k == n:
            upper = Decimal(1)
        else:
            lo, hi = Decimal(0), Decimal(1)
            for _ in range(48):
                mid = (lo + hi) / 2
                if _cdf_le(n, mid, k) > tail: lo = mid
                else: hi = mid
            upper = (lo + hi) / 2
    return [format(lower, "f"), format(upper, "f")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", required=True, type=Path)
    parser.add_argument("--selection-receipt", required=True, type=Path)
    parser.add_argument("--controller-command", required=True, type=Path)
    parser.add_argument("--seed-domain", required=True)
    parser.add_argument("--sequences-per-cell", required=True, type=int)
    parser.add_argument("--confidence-family", required=True)
    parser.add_argument("--cell-workers", type=int, default=len(GAMES) * len(WORLDS))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.seed_domain != "power-confirmation" or args.confidence_family != "0.95":
        raise ValueError("T13 seed domain/confidence family mismatch")
    design = json.loads(args.design.read_text(encoding="utf-8"))
    receipt = json.loads(args.selection_receipt.read_text(encoding="utf-8"))
    command = json.loads(args.controller_command.read_text(encoding="utf-8"))
    if command.get("artifact_type") != "phase4_scientific_controller_command" or command.get("protocol") != "phase4_scientific_single_sequence_json_v1":
        raise ValueError("controller interface is not the frozen scientific protocol")
    if receipt.get("status") != "PASS" or receipt.get("selected_design_id") != design.get("design_id"):
        raise ValueError("selection receipt does not bind the selected design")
    identity = command["controller_identity"]
    if design.get("controller_identity", {}).get("controller_identity_id") != identity["controller_identity_id"]:
        raise ValueError("design is not bound to the frozen controller identity")
    args.output.mkdir(parents=True, exist_ok=False)
    cells, all_commitments = collect_cells(
        design=design,
        identity=identity,
        argv=command["argv"],
        seed_domain=args.seed_domain,
        sequences_per_cell=args.sequences_per_cell,
        output=args.output,
        cell_workers=args.cell_workers,
    )
    control = {"schema_version":"1.0.0", "artifact_type":"phase4_power_confirmation_raw",
               "design_id":design["design_id"], "seed_domain":args.seed_domain,
               "controller_identity":identity, "sequences_per_cell":args.sequences_per_cell,
               "seed_set_sha256":sha(canonical(sorted(all_commitments))), "cells":cells}
    (args.output / "raw-control.json").write_bytes(canonical(control))
    summary_cells = []
    for cell in cells:
        count = cell["success_count"]
        summary_cells.append({"game":cell["game"], "world":cell["world"],
                              "sequence_count":cell["sequence_count"], "success_count":count,
                              "sequence_rate_estimate":format(Decimal(count) / Decimal(args.sequences_per_cell), "f"),
                              "sequence_rate_simultaneous_interval":_cp_interval(args.sequences_per_cell, count),
                              "seed_set_sha256":cell["seed_set_sha256"]})
    summary = {"schema_version":"1.0.0", "artifact_type":"phase4_power_confirmation_summary",
               "design_id":design["design_id"], "controller_identity":identity,
               "confidence_family":"simultaneous_95_percent_clopper_pearson",
               "bonferroni_two_sided_tail":"0.05/(2*8)", "cells":summary_cells}
    (args.output / "summary.json").write_bytes(canonical(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
