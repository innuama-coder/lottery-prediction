"""Synthetic-only P2-01 hardware benchmark; never reads lottery history."""

from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import json
import math
import os
import random
import statistics
import sys
import time
from typing import Any

from .serialization import canonical_json_bytes


def _simulate(seed: int, worlds: int, draws_per_world: int, biased: bool) -> float:
    rng = random.Random(seed)
    accumulator = 0.0
    null_probability = 5.0 / 35.0
    for _ in range(worlds):
        inclusions = 0
        for _ in range(draws_per_world):
            if biased and rng.random() < 0.20:
                numbers = {1, *rng.sample(range(2, 36), 4)}
            else:
                numbers = set(rng.sample(range(1, 36), 5))
            inclusions += 1 in numbers
        accumulator += abs(inclusions / draws_per_world - null_probability)
    return accumulator


def _timed(seed: int, worlds: int, draws_per_world: int, biased: bool) -> tuple[float, float]:
    started = time.perf_counter()
    checksum = _simulate(seed, worlds, draws_per_world, biased)
    return time.perf_counter() - started, checksum


def _partition(total: int, workers: int) -> list[int]:
    quotient, remainder = divmod(total, workers)
    return [quotient + (1 if index < remainder else 0) for index in range(workers)]


def _parallel_elapsed(seed: int, worlds: int, draws_per_world: int, workers: int) -> tuple[float, float]:
    sizes = [size for size in _partition(worlds, workers) if size]
    started = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_simulate, seed + index * 1009, size, draws_per_world, index % 2 == 1)
            for index, size in enumerate(sizes)
        ]
        checksum = sum(future.result() for future in futures)
    return time.perf_counter() - started, checksum


def _peak_working_set_bytes() -> int:
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return int(counters.PeakWorkingSetSize)
    import resource

    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def run_benchmark(*, worlds: int, draws_per_world: int, repeats: int, workers: int, seed: int) -> dict[str, Any]:
    if worlds < 1000 or draws_per_world < 1 or repeats < 1 or workers < 1:
        raise ValueError("benchmark requires worlds>=1000, draws_per_world>=1, repeats>=1 and workers>=1")
    null_times: list[float] = []
    bias_times: list[float] = []
    checksums: list[float] = []
    for repeat in range(repeats):
        null_elapsed, null_checksum = _timed(seed + repeat * 17, worlds, draws_per_world, False)
        bias_elapsed, bias_checksum = _timed(seed + repeat * 17 + 1, worlds, draws_per_world, True)
        null_times.append(null_elapsed)
        bias_times.append(bias_elapsed)
        checksums.extend((null_checksum, bias_checksum))
    parallel_elapsed, parallel_checksum = _parallel_elapsed(seed + 99991, worlds * 2, draws_per_world, workers)
    sequential_elapsed = statistics.median(null_times) + statistics.median(bias_times)
    efficiency = min(1.0, sequential_elapsed / (parallel_elapsed * workers))
    if not math.isfinite(efficiency) or efficiency <= 0:
        raise RuntimeError("invalid measured parallel efficiency")

    peak_memory = _peak_working_set_bytes()
    payload: dict[str, Any] = {
        "status": "PASS",
        "synthetic_only": True,
        "seed": seed,
        "worlds_per_scenario": worlds,
        "draws_per_world": draws_per_world,
        "repeats": repeats,
        "null_wall_seconds_per_1000_worlds": statistics.median(null_times) * 1000.0 / worlds,
        "bias_wall_seconds_per_1000_worlds": statistics.median(bias_times) * 1000.0 / worlds,
        "peak_memory_bytes": peak_memory,
        "memory_metric": "coordinator_process_peak_working_set_bytes",
        "artifact_bytes": 1,
        "parallel_workers": workers,
        "parallel_efficiency_factor": efficiency,
        "formal_estimate_allowed": True,
        "diagnostic_checksum": round(sum(checksums) + parallel_checksum, 12),
    }
    payload["artifact_bytes"] = len(canonical_json_bytes(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worlds", type=int, default=1000)
    parser.add_argument("--draws-per-world", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--seed", type=int, default=20260805)
    args = parser.parse_args()
    payload = run_benchmark(
        worlds=args.worlds,
        draws_per_world=args.draws_per_world,
        repeats=args.repeats,
        workers=args.workers,
        seed=args.seed,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
