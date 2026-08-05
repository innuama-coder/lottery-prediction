"""G2-only deterministic no-change comparison."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lottery_data.serialization import canonical_jsonl_bytes, sha256_bytes

from .transform import TransformResult


class DeltaOutsideG2Scope(RuntimeError):
    """The candidate differs from the frozen current release."""


def _release_rows(current_release: Mapping[str, Any] | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(current_release, Path):
        def read_jsonl(name: str) -> list[dict[str, Any]]:
            return [
                json.loads(line)
                for line in (current_release / name).read_text(encoding="utf-8").splitlines()
                if line
            ]
        return read_jsonl("draws.jsonl"), read_jsonl("observations.jsonl")
    try:
        return list(current_release["draws"]), list(current_release["observations"])
    except (KeyError, TypeError) as exc:
        raise DeltaOutsideG2Scope("current release requires draws and observations") from exc


def compare_no_change(
    transform: TransformResult,
    current_release: Mapping[str, Any] | Path,
) -> dict[str, Any]:
    try:
        current_draws, current_observations = _release_rows(current_release)
        candidate_draws = list(transform.draws)
        candidate_observations = list(transform.observations_selected)
        actual = {
            "draw_count": len(current_draws),
            "observation_count": len(current_observations),
            "draw_keys": sorted((item["game"], item["issue_id"]) for item in current_draws),
            "observation_keys": sorted(item["observation_id"] for item in current_observations),
            "draws_sha256": sha256_bytes(canonical_jsonl_bytes(
                current_draws, sort_keys=("game", "issue_id", "revision_id"),
            )),
            "observations_sha256": sha256_bytes(canonical_jsonl_bytes(
                current_observations,
                sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"),
            )),
        }
        expected = {
            "draw_count": 400,
            "observation_count": 800,
            "draw_keys": sorted((item["game"], item["issue_id"]) for item in candidate_draws),
            "observation_keys": sorted(item["observation_id"] for item in candidate_observations),
            "draws_sha256": transform.output_hashes["draws"],
            "observations_sha256": transform.output_hashes["release_observations"],
        }
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise DeltaOutsideG2Scope(f"invalid current/candidate no-change surface: {exc}") from exc
    candidate_shape = len(candidate_draws) == 400 and len(candidate_observations) == 800
    candidate_unique = (
        len(expected["draw_keys"]) == len(set(expected["draw_keys"]))
        and len(expected["observation_keys"]) == len(set(expected["observation_keys"]))
    )
    if not candidate_shape or not candidate_unique or actual != expected:
        raise DeltaOutsideG2Scope(f"incremental delta is outside G2 no-change scope: expected={expected}, actual={actual}")
    return {"status": "no_change", "expected": expected, "actual": actual}
