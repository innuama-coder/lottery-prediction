from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from numpy.typing import NDArray

from .intervals import clopper_pearson_one_sided
from .serialization import canonical_json_bytes
from .statistics import PRIMARY_FAMILIES
from .vectorized import BatchDraws, calculate_statistics_batch, generate_batch, iter_generated_batches


def domain_seed(base: int, domain: str) -> int:
    digest = hashlib.sha256(f"{base}:{domain}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def simulate_prefix_statistics(
    rule_map: dict[str, Any], *, worlds: int, sample_sizes: list[int], family: str,
    effect: float, seed: int, chunk_worlds: int = 128, issue_ids_by_n: dict[int, list[int]] | None = None,
) -> dict[int, dict[str, dict[str, NDArray[np.float64]]]]:
    result: dict[int, dict[str, dict[str, list[NDArray[np.float64]]]]] = {
        n: {name: {"statistic": [], "effect": []} for name in (*PRIMARY_FAMILIES, "negative_control")}
        for n in sample_sizes
    }
    if family == "temporal_instability":
        for n in sample_sizes:
            issues = None if issue_ids_by_n is None else issue_ids_by_n[n]
            for batch in iter_generated_batches(rule_map, worlds=worlds, draws=n, family=family, effect=effect, seed=domain_seed(seed, f"n={n}"), chunk_worlds=chunk_worlds, issue_ids=issues):
                stats = calculate_statistics_batch(batch, rule_map, chunk_worlds=chunk_worlds)
                for name in result[n]:
                    for field in ("statistic", "effect"):
                        result[n][name][field].append(stats[name][field])
    else:
        maximum = max(sample_sizes)
        issues = None if issue_ids_by_n is None else issue_ids_by_n[maximum]
        for batch in iter_generated_batches(rule_map, worlds=worlds, draws=maximum, family=family, effect=effect, seed=seed, chunk_worlds=chunk_worlds, issue_ids=issues):
            for n in sample_sizes:
                prefix = BatchDraws(batch.front_numbers[:, :n], batch.back_numbers[:, :n], batch.issue_ids[:n])
                stats = calculate_statistics_batch(prefix, rule_map, chunk_worlds=chunk_worlds)
                for name in result[n]:
                    for field in ("statistic", "effect"):
                        result[n][name][field].append(stats[name][field])
    return {
        n: {name: {field: np.concatenate(parts) for field, parts in fields.items()} for name, fields in families.items()}
        for n, families in result.items()
    }


def flatten_corpus(prefix: str, game: str, values: dict[int, dict[str, dict[str, NDArray[np.float64]]]]) -> dict[str, NDArray[np.float64]]:
    return {
        f"{prefix}.{game}.n{n}.{family}.{field}": array.astype("<f8", copy=False)
        for n, families in values.items()
        for family, fields in families.items()
        for field, array in fields.items()
    }


def checkpointed_prefix_statistics(
    rule_map: dict[str, Any], *, worlds: int, sample_sizes: list[int], family: str,
    effect: float, seed: int, checkpoint_root: Path, chunk_worlds: int,
    issue_ids_by_n: dict[int, list[int]] | None = None,
    interrupt_after_new_batches: int | None = None,
) -> tuple[dict[int, dict[str, dict[str, NDArray[np.float64]]]], dict[str, Any]]:
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    batch_count = math.ceil(worlds / chunk_worlds)
    configuration = {
        "checkpoint_format_version": "2.0.0",
        "rule_map": rule_map,
        "worlds": worlds,
        "sample_sizes": sample_sizes,
        "family": family,
        "effect": effect,
        "seed": seed,
        "chunk_worlds": chunk_worlds,
        "issue_ids_by_n": {str(key): value for key, value in sorted((issue_ids_by_n or {}).items())},
        "batch_count": batch_count,
    }
    configuration_bytes = canonical_json_bytes(configuration)
    configuration_sha256 = hashlib.sha256(configuration_bytes).hexdigest()
    configuration_path = checkpoint_root / "checkpoint-config.json"
    expected_bin_names = {f"batch-{index:05d}.bin" for index in range(batch_count)}
    expected_meta_names = {f"batch-{index:05d}.json" for index in range(batch_count)}
    existing_batch_names = {path.name for path in checkpoint_root.glob("batch-*")}
    unexpected = existing_batch_names - expected_bin_names - expected_meta_names
    if unexpected:
        raise ValueError(f"checkpoint directory contains unexpected batch files: {sorted(unexpected)}")
    if configuration_path.exists():
        if configuration_path.read_bytes() != configuration_bytes:
            raise ValueError("checkpoint configuration fingerprint mismatch")
    else:
        if existing_batch_names:
            raise ValueError("checkpoint batches exist without a frozen configuration")
        configuration_path.write_bytes(configuration_bytes)
    new_batches = 0
    reused_batches = 0
    identities = []
    for batch_index in range(batch_count):
        count = min(chunk_worlds, worlds - batch_index * chunk_worlds)
        batch_path = checkpoint_root / f"batch-{batch_index:05d}.bin"
        metadata_path = checkpoint_root / f"batch-{batch_index:05d}.json"
        batch_seed = domain_seed(seed, f"checkpoint-batch={batch_index}")
        expected_metadata = {
            "schema_version": "1.0.0",
            "artifact_type": "phase2_checkpoint_batch",
            "configuration_sha256": configuration_sha256,
            "batch_index": batch_index,
            "worlds": count,
            "seed": batch_seed,
        }
        if batch_path.exists() != metadata_path.exists():
            raise ValueError(f"checkpoint batch/metadata pair is incomplete: {batch_index}")
        if batch_path.exists():
            read_array_bundle(batch_path)
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid checkpoint metadata for batch {batch_index}") from exc
            observed_hash = hashlib.sha256(batch_path.read_bytes()).hexdigest()
            if metadata != {**expected_metadata, "bundle_sha256": observed_hash}:
                raise ValueError(f"checkpoint batch identity mismatch: {batch_index}")
            reused_batches += 1
        else:
            values = simulate_prefix_statistics(rule_map, worlds=count, sample_sizes=sample_sizes, family=family, effect=effect, seed=batch_seed, chunk_worlds=count, issue_ids_by_n=issue_ids_by_n)
            arrays = flatten_corpus("batch", str(rule_map["game"]), values)
            write_array_bundle(batch_path, arrays)
            observed_hash = hashlib.sha256(batch_path.read_bytes()).hexdigest()
            metadata_path.write_bytes(canonical_json_bytes({**expected_metadata, "bundle_sha256": observed_hash}))
            new_batches += 1
        identities.append({"batch_index": batch_index, "path": batch_path.as_posix(), "metadata_path": metadata_path.as_posix(), "sha256": observed_hash, "worlds": count, "seed": batch_seed, "configuration_sha256": configuration_sha256})
        if interrupt_after_new_batches is not None and new_batches >= interrupt_after_new_batches:
            ledger = {"status": "controlled_interruption", "configuration_sha256": configuration_sha256, "expected_batches": batch_count, "completed_batches": batch_index + 1, "new_batches": new_batches, "reused_batches": reused_batches, "batches": identities}
            (checkpoint_root / "ledger.json").write_bytes(canonical_json_bytes(ledger))
            raise KeyboardInterrupt("controlled Phase 2 checkpoint interruption")
    actual_bin_names = {path.name for path in checkpoint_root.glob("batch-*.bin")}
    actual_meta_names = {path.name for path in checkpoint_root.glob("batch-*.json")}
    if actual_bin_names != expected_bin_names or actual_meta_names != expected_meta_names:
        raise ValueError("checkpoint batch inventory differs from the frozen expected inventory")
    aggregate: dict[str, list[NDArray[np.float64]]] = {}
    for row in identities:
        for key, array in read_array_bundle(Path(row["path"])).items():
            aggregate.setdefault(key, []).append(array)
    combined = {key: np.concatenate(parts) for key, parts in aggregate.items()}
    aggregate_path = checkpoint_root / "aggregate.bin"
    meta = write_array_bundle(aggregate_path, combined)
    prefix = f"batch.{rule_map['game']}."
    nested: dict[int, dict[str, dict[str, NDArray[np.float64]]]] = {
        n: {name: {"statistic": np.empty(0), "effect": np.empty(0)} for name in (*PRIMARY_FAMILIES, "negative_control")}
        for n in sample_sizes
    }
    for key, array in combined.items():
        remainder = key.removeprefix(prefix)
        n_text, family_name, field = remainder.split(".")
        nested[int(n_text[1:])][family_name][field] = array
    ledger = {
        "status": "complete", "configuration_sha256": configuration_sha256,
        "expected_batches": batch_count, "completed_batches": len(actual_bin_names),
        "new_batches": new_batches, "reused_batches": reused_batches,
        "missing_batches": len(expected_bin_names - actual_bin_names), "duplicate_batches": 0,
        "aggregate_path": aggregate_path.as_posix(), "aggregate_sha256": meta["normalized_sha256"], "batches": identities,
    }
    (checkpoint_root / "ledger.json").write_bytes(canonical_json_bytes(ledger))
    return nested, ledger


def write_array_bundle(path: Path, arrays: dict[str, NDArray[np.float64]]) -> dict[str, Any]:
    ordered = {key: np.ascontiguousarray(arrays[key], dtype="<f8") for key in sorted(arrays)}
    offset = 0
    descriptors = []
    for key, array in ordered.items():
        descriptors.append({"key": key, "dtype": "<f8", "shape": list(array.shape), "offset": offset, "nbytes": array.nbytes})
        offset += array.nbytes
    header = canonical_json_bytes({"schema_version": "1.0.0", "arrays": descriptors})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"P2ARRAY1")
        handle.write(struct.pack("<Q", len(header)))
        handle.write(header)
        for array in ordered.values():
            handle.write(array.tobytes(order="C"))
    return {"array_count": len(ordered), "data_bytes": offset, "normalized_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_array_bundle(path: Path) -> dict[str, NDArray[np.float64]]:
    raw = path.read_bytes()
    if raw[:8] != b"P2ARRAY1":
        raise ValueError("invalid Phase 2 array bundle magic")
    header_size = struct.unpack("<Q", raw[8:16])[0]
    header = json.loads(raw[16:16 + header_size])
    data_start = 16 + header_size
    result: dict[str, NDArray[np.float64]] = {}
    for row in header["arrays"]:
        start = data_start + row["offset"]
        stop = start + row["nbytes"]
        result[row["key"]] = np.frombuffer(raw[start:stop], dtype=row["dtype"]).reshape(row["shape"]).copy()
    return result


def empirical_p(reference: NDArray[np.float64], observed: NDArray[np.float64]) -> NDArray[np.float64]:
    ordered = np.sort(reference)
    indices = np.searchsorted(ordered, observed, side="left")
    return (len(ordered) - indices + 1.0) / (len(ordered) + 1.0)


def holm_adjust_matrix(pvalues: NDArray[np.float64]) -> NDArray[np.float64]:
    order = np.argsort(pvalues, axis=1, kind="stable")
    sorted_p = np.take_along_axis(pvalues, order, axis=1)
    multipliers = np.arange(pvalues.shape[1], 0, -1, dtype=np.float64)
    adjusted_sorted = np.maximum.accumulate(np.minimum(1.0, sorted_p * multipliers), axis=1)
    adjusted = np.empty_like(adjusted_sorted)
    np.put_along_axis(adjusted, order, adjusted_sorted, axis=1)
    return adjusted


def central_acceptance(values: NDArray[np.float64]) -> tuple[float, float]:
    lower, upper = np.quantile(values, [0.025, 0.975], method="inverted_cdf")
    return float(lower), float(upper)


def calibrate_cross_zone_q(rule_map: dict[str, Any], target_v: float, seed: int, tolerance: float = 0.015) -> dict[str, float]:
    if target_v == 0:
        return {"target_v": 0.0, "mixture_q": 0.0, "calibrated_v": 0.0, "absolute_error": 0.0}
    low, high = 0.0, 1.0
    best = (float("inf"), 0.0, 0.0)
    for iteration in range(14):
        q = (low + high) / 2.0
        batch = generate_batch(rule_map, worlds=1, draws=100000, family="cross_zone_dependence", effect=q, seed=domain_seed(seed, "common-random-numbers"))
        value = float(calculate_statistics_batch(batch, rule_map, chunk_worlds=1)["cross_zone_dependence"]["effect"][0])
        error = abs(value - target_v)
        if error < best[0]:
            best = (error, q, value)
        if value < target_v:
            low = q
        else:
            high = q
    if best[0] > tolerance:
        raise ValueError(f"cross-zone mapping misses tolerance: target={target_v}, best={best}")
    return {"target_v": target_v, "mixture_q": best[1], "calibrated_v": best[2], "absolute_error": best[0]}


def scenario_generator_effect(family: str, requested_effect: float, cross_mapping: dict[float, float] | None = None) -> float:
    if family != "cross_zone_dependence":
        return requested_effect
    if cross_mapping is None or requested_effect not in cross_mapping:
        raise ValueError("cross-zone effect has no frozen q mapping")
    return cross_mapping[requested_effect]


def neyman_grid_confidence_set(observed: float, bands: Iterable[dict[str, Any]]) -> dict[str, Any]:
    accepted = [float(row["effect"]) for row in bands if row["acceptance_lower"] <= observed <= row["acceptance_upper"]]
    if not accepted:
        return {"state": "empty_conservative", "grid_values": [], "hull": [0.0, max(float(row["effect"]) for row in bands)]}
    return {"state": "identified_grid_set", "grid_values": accepted, "hull": [min(accepted), max(accepted)]}


def coverage_verdict(successes: int, trials: int) -> dict[str, float | int | bool]:
    lower, upper = clopper_pearson_one_sided(successes, trials)
    return {"successes": successes, "trials": trials, "estimate": successes / trials, "one_sided_95_lower": lower, "one_sided_95_upper": upper, "pass": lower >= 0.93}
