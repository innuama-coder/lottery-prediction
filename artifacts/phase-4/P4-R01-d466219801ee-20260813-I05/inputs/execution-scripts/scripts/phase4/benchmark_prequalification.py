#!/usr/bin/env python3
"""Benchmark the frozen non-scientific qualification evidence path.

This program never derives or consumes development, power-confirmation, or
formal-qualification seeds.  It measures a deterministic black-box controller,
checkpoint, lossless shard, manifest recomputation, and evidence-return path,
then extrapolates only by the fixed workload formula with a 25% margin.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - exercised on Windows
    resource = None


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_DOMAINS = ["development", "power-confirmation", "formal-qualification"]


class BenchmarkViolation(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return digest(path.read_bytes())


def peak_rss_kib() -> int:
    if resource is not None:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss + resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # The Windows standard library does not expose child peak RSS.  Report the
    # current controller process peak working set, which is the stable native
    # equivalent available without adding a runtime dependency.
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_current_process = ctypes.windll.kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    get_process_memory_info.restype = wintypes.BOOL
    if not get_process_memory_info(get_current_process(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return counters.PeakWorkingSetSize // 1024


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkViolation(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise BenchmarkViolation(f"JSON root must be object: {path}")
    return value


def write_once(path: Path, value: dict[str, Any] | bytes) -> None:
    encoded = value if isinstance(value, bytes) else canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise BenchmarkViolation(f"immutable benchmark identity reuse: {path}")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def validate_inputs(registry_path: Path, command_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    registry = load_object(registry_path)
    required = {
        "schema_version", "artifact_type", "benchmark_fixture_id", "non_scientific",
        "qualification_seed_domain", "forbidden_seed_domains", "input", "controller_fixture",
        "controller_source", "representative_batches",
    }
    if set(registry) != required or registry["schema_version"] != "1.0.0" or registry["artifact_type"] != "phase4_benchmark_fixture_registry":
        raise BenchmarkViolation("benchmark fixture registry shape or identity mismatch")
    if registry["non_scientific"] is not True or registry["qualification_seed_domain"] is not None:
        raise BenchmarkViolation("benchmark fixture is not explicitly non-scientific")
    if registry["forbidden_seed_domains"] != FORBIDDEN_DOMAINS:
        raise BenchmarkViolation("qualification seed-domain exclusion changed")
    identity_body = {key: value for key, value in registry.items() if key != "benchmark_fixture_id"}
    expected_id = "benchmark-fixture-v1:" + digest(canonical(identity_body))
    if registry["benchmark_fixture_id"] != expected_id:
        raise BenchmarkViolation("benchmark fixture content identity mismatch")
    input_row = registry["input"]
    controller_row = registry["controller_fixture"]
    source_row = registry["controller_source"]
    if set(input_row) != {"path", "sha256", "sequence_count"} or set(controller_row) != {"path", "sha256"} or set(source_row) != {"path", "sha256"}:
        raise BenchmarkViolation("benchmark input binding shape mismatch")
    input_path = (ROOT / input_row["path"]).resolve()
    input_path.relative_to(ROOT)
    bound_command = (ROOT / controller_row["path"]).resolve()
    bound_command.relative_to(ROOT)
    bound_source = (ROOT / source_row["path"]).resolve()
    bound_source.relative_to(ROOT)
    if sha256_file(input_path) != input_row["sha256"] or sha256_file(bound_command) != controller_row["sha256"] or sha256_file(bound_source) != source_row["sha256"]:
        raise BenchmarkViolation("benchmark registry file binding mismatch")
    if sha256_file(command_path) != controller_row["sha256"]:
        raise BenchmarkViolation("selected controller command differs from frozen fixture command bytes")
    supplied = load_object(input_path)
    if supplied.get("non_scientific") is not True or supplied.get("qualification_seed_domain") is not None:
        raise BenchmarkViolation("benchmark input seed-domain declaration invalid")
    if len(supplied.get("sequences", [])) != input_row["sequence_count"]:
        raise BenchmarkViolation("benchmark input sequence count mismatch")
    command = load_object(command_path)
    if set(command) != {"schema_version", "artifact_type", "argv", "protocol", "non_scientific", "qualification_seed_domain"}:
        raise BenchmarkViolation("controller command shape is not closed")
    if command["artifact_type"] != "phase4_benchmark_controller_command" or command["protocol"] != "json_stdin_stdout_v1":
        raise BenchmarkViolation("controller command protocol mismatch")
    if command["non_scientific"] is not True or command["qualification_seed_domain"] is not None:
        raise BenchmarkViolation("controller command is not seed-domain isolated")
    if not isinstance(command["argv"], list) or not command["argv"] or any(not isinstance(item, str) or not item for item in command["argv"]):
        raise BenchmarkViolation("controller argv invalid")
    required_units = {"qualification_sequence", "black_box_controller", "checkpoint_every_10", "lossless_shard", "manifest_recompute", "evidence_return"}
    rows = registry["representative_batches"]
    if not isinstance(rows, list) or {row.get("unit_id") for row in rows} != required_units:
        raise BenchmarkViolation("representative benchmark units incomplete")
    if any(set(row) != {"unit_id", "sequence_count"} or row["sequence_count"] != len(supplied["sequences"]) for row in rows):
        raise BenchmarkViolation("representative batch size mismatch")
    return registry, command, supplied


def percentile(values: list[int], quantile: int) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil((quantile * len(ordered)) / 100) - 1)]


def execute_sample(
    *, registry: dict[str, Any], command: dict[str, Any], supplied: dict[str, Any],
    cycles: int, root: Path, run_id: str,
) -> dict[str, Any]:
    payload = {
        "benchmark_fixture_id": registry["benchmark_fixture_id"],
        "cycles_per_sequence": cycles,
        "sequences": supplied["sequences"],
    }
    input_bytes = canonical(payload)
    start = time.perf_counter_ns()
    argv = list(command["argv"])
    if os.name == "nt" and argv[0] in {"python3", "/usr/bin/python3"}:
        argv[0] = sys.executable
    process = subprocess.run(argv, cwd=ROOT, input=input_bytes, capture_output=True, check=False)
    controller_end = time.perf_counter_ns()
    if process.returncode != 0 or process.stderr:
        raise BenchmarkViolation(f"black-box controller failed for {run_id}")
    controller_output = json.loads(process.stdout)
    if controller_output.get("benchmark_fixture_id") != registry["benchmark_fixture_id"]:
        raise BenchmarkViolation("controller output fixture identity mismatch")
    if controller_output.get("non_scientific") is not True or controller_output.get("qualification_seed_domain") is not None:
        raise BenchmarkViolation("controller output seed-domain declaration invalid")
    terminals = controller_output.get("terminals")
    if not isinstance(terminals, list) or len(terminals) != len(supplied["sequences"]):
        raise BenchmarkViolation("controller terminal count mismatch")
    checkpoint = {
        "schema_version": "1.0.0", "artifact_type": "phase4_benchmark_checkpoint",
        "benchmark_fixture_id": registry["benchmark_fixture_id"], "run_id": run_id,
        "next_sequence_ordinal": len(terminals), "checkpoint_every_sequences": 10,
        "controller_output_sha256": digest(process.stdout), "non_scientific": True,
        "qualification_seed_domain": None,
    }
    checkpoint_bytes = canonical(checkpoint)
    checkpoint_end = time.perf_counter_ns()
    shard = gzip.compress(process.stdout, compresslevel=9, mtime=0)
    if gzip.decompress(shard) != process.stdout:
        raise BenchmarkViolation("lossless benchmark shard did not round-trip")
    shard_end = time.perf_counter_ns()
    run_root = root / run_id
    write_once(run_root / "controller-input.json", input_bytes)
    write_once(run_root / "controller-output.json", process.stdout)
    write_once(run_root / "checkpoint.json", checkpoint_bytes)
    write_once(run_root / "controller-output.json.gz", shard)
    file_rows = []
    for name in ("controller-input.json", "controller-output.json", "checkpoint.json", "controller-output.json.gz"):
        path = run_root / name
        file_rows.append({"path": name, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    manifest = {
        "schema_version": "1.0.0", "artifact_type": "phase4_benchmark_run_manifest",
        "benchmark_fixture_id": registry["benchmark_fixture_id"], "run_id": run_id,
        "files": file_rows, "non_scientific": True, "qualification_seed_domain": None,
    }
    write_once(run_root / "manifest.json", manifest)
    manifest_end = time.perf_counter_ns()
    returned = b"".join((run_root / row["path"]).read_bytes() for row in file_rows)
    return_end = time.perf_counter_ns()
    if [(row["path"], sha256_file(run_root / row["path"]), (run_root / row["path"]).stat().st_size) for row in file_rows] != [
        (row["path"], row["sha256"], row["bytes"]) for row in file_rows
    ]:
        raise BenchmarkViolation("manifest recomputation mismatch")
    return {
        "run_id": run_id,
        "sequence_count": len(terminals),
        "observation_count": len(terminals) * cycles,
        "controller_ns": controller_end - start,
        "checkpoint_ns": checkpoint_end - controller_end,
        "lossless_shard_ns": shard_end - checkpoint_end,
        "manifest_ns": manifest_end - shard_end,
        "evidence_return_ns": return_end - manifest_end,
        "total_ns": return_end - start,
        "rss_kib": peak_rss_kib(),
        "file_count": len(file_rows) + 1,
        "uncompressed_bytes": len(process.stdout),
        "compressed_bytes": len(shard),
        "evidence_return_bytes": len(returned),
        "evidence_return_sha256": digest(returned),
        "manifest_sha256": sha256_file(run_root / "manifest.json"),
    }


def workload_projection(samples: list[dict[str, Any]], *, target_sequences: int, cycles: int) -> dict[str, Any]:
    batch = samples[0]["sequence_count"]
    factor_num, factor_den = target_sequences, batch
    p95_ns = percentile([row["total_ns"] for row in samples], 95)
    p95_bytes = percentile([row["evidence_return_bytes"] for row in samples], 95)
    p95_files = percentile([row["file_count"] for row in samples], 95)
    return {
        "target_sequences": target_sequences,
        "target_observations": target_sequences * cycles,
        "extrapolation_formula": "ceil(representative_p95 * target_sequences / representative_sequences * 1.25)",
        "budget_time_ns": math.ceil(p95_ns * factor_num * 5 / (factor_den * 4)),
        "budget_evidence_bytes": math.ceil(p95_bytes * factor_num * 5 / (factor_den * 4)),
        "budget_file_count": math.ceil(p95_files * factor_num * 5 / (factor_den * 4)),
        "checkpoint_every_sequences": 10,
        "margin": "25%",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller-command", required=True, type=Path)
    parser.add_argument("--benchmark-fixtures", required=True, type=Path)
    parser.add_argument("--target-development-sequences", required=True, type=int)
    parser.add_argument("--target-power-sequences", required=True, type=int)
    parser.add_argument("--cycles-per-sequence", required=True, type=int)
    parser.add_argument("--runs", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.runs != 20 or args.target_development_sequences != 48000 or args.target_power_sequences != 160000 or args.cycles_per_sequence != 150:
            raise BenchmarkViolation("frozen preparation workload arguments changed")
        output = args.output.resolve()
        output.relative_to(ROOT)
        registry_path = args.benchmark_fixtures.resolve(); registry_path.relative_to(ROOT)
        command_path = args.controller_command.resolve(); command_path.relative_to(ROOT)
        registry, command, supplied = validate_inputs(registry_path, command_path)
        warmup = execute_sample(registry=registry, command=command, supplied=supplied, cycles=args.cycles_per_sequence, root=output / "warm-up", run_id="warm-up")
        samples = [
            execute_sample(registry=registry, command=command, supplied=supplied, cycles=args.cycles_per_sequence, root=output / "runs", run_id=f"run-{ordinal:02d}")
            for ordinal in range(1, args.runs + 1)
        ]
        write_once(output / "warm-up.json", warmup)
        write_once(output / "samples.json", {"samples": samples})
        receipt = {
            "schema_version": "1.0.0", "artifact_type": "phase4_prequalification_benchmark_receipt",
            "status": "PASS", "terminal": "PREQUALIFICATION_BENCHMARK_PASS",
            "benchmark_fixture_id": registry["benchmark_fixture_id"],
            "benchmark_fixture_registry_sha256": sha256_file(registry_path),
            "controller_command_sha256": sha256_file(command_path),
            "non_scientific": True, "qualification_seed_domain": None,
            "qualification_seed_reference_count": 0, "qualification_terminal_count": 0,
            "warm_up_count": 1, "measured_run_count": len(samples),
            "representative_sequence_count": samples[0]["sequence_count"],
            "representative_observation_count": samples[0]["observation_count"],
            "p50_total_ns": percentile([row["total_ns"] for row in samples], 50),
            "p95_total_ns": percentile([row["total_ns"] for row in samples], 95),
            "p95_rss_kib": percentile([row["rss_kib"] for row in samples], 95),
            "p95_file_count": percentile([row["file_count"] for row in samples], 95),
            "p95_uncompressed_bytes": percentile([row["uncompressed_bytes"] for row in samples], 95),
            "p95_compressed_bytes": percentile([row["compressed_bytes"] for row in samples], 95),
            "p95_evidence_return_bytes": percentile([row["evidence_return_bytes"] for row in samples], 95),
            "evidence_return_hash_match_count": sum(len(row["evidence_return_sha256"]) == 64 for row in samples),
            "manifest_recompute_match_count": sum(len(row["manifest_sha256"]) == 64 for row in samples),
            "development_projection": workload_projection(samples, target_sequences=args.target_development_sequences, cycles=args.cycles_per_sequence),
            "power_projection": workload_projection(samples, target_sequences=args.target_power_sequences, cycles=args.cycles_per_sequence),
            "selection_or_gate_reference_count": 0,
        }
        write_once(output / "receipt.json", receipt)
        sys.stdout.buffer.write(canonical(receipt))
        return 0
    except (BenchmarkViolation, OSError, ValueError, subprocess.SubprocessError) as exc:
        payload = {"status": "HOLD", "terminal": "HOLD_PREQUALIFICATION_BUDGET", "exit_code": 20, "error": str(exc)}
        sys.stdout.buffer.write(canonical(payload))
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
