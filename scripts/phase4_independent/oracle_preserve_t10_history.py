from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path.cwd().resolve()
PREP = Path("artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items")
ACTOR = "p4-independent-oracle-author-i01"
SESSION = "/root/independent_oracle_author"
SOURCE_COMMIT = "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"{label}: expected one replacement, observed {text.count(old)}")
    return text.replace(old, new, 1)


def _expected(data: bytes, sha256: str, size: int, label: str) -> bytes:
    if len(data) != size or _sha_bytes(data) != sha256:
        raise ValueError(f"{label}: reconstruction mismatch bytes={len(data)} sha256={_sha_bytes(data)}")
    return data


def _derive() -> dict[str, dict[str, Any]]:
    runner_current = Path("scripts/phase4_independent/run_known_answers.py").read_text(encoding="utf-8")
    math_current = Path("scripts/phase4_independent/oracle_math.py").read_text(encoding="utf-8")
    finalize_current = Path("scripts/phase4_independent/oracle_finalize_t10.py").read_text(encoding="utf-8")

    runner_i03 = runner_current
    runner_i03 = _replace_once(runner_i03, "    guard_vectors,\n", "", "runner I03 guard import")
    runner_i03 = _replace_once(runner_i03, "    m0_real_rule_oracle,\n", "", "runner I03 M0 import")
    runner_i03 = _replace_once(runner_i03, "    m0_results = [m0_real_rule_oracle(game, games[game], contract[\"probability\"][\"top_k\"]) for game in (\"ssq\", \"dlt\")]\n", "", "runner I03 M0 generation")
    runner_i03 = _replace_once(
        runner_i03,
        """    _write_new(args.output / \"real-rule-m0.json\", {\n        \"schema_version\": \"1.0.0\",\n        \"artifact_type\": \"phase4_independent_real_rule_m0_known_answers\",\n        \"games\": m0_results,\n        \"status\": \"PASS\",\n    })\n    _write_new(args.output / \"guard-vectors.json\", {\n        \"schema_version\": \"1.0.0\",\n        \"artifact_type\": \"phase4_independent_probability_ranking_guard_vectors\",\n        **guard_vectors(),\n        \"status\": \"PASS\",\n    })\n""",
        "",
        "runner I03 extra vectors",
    )
    runner_i03_bytes = _expected(runner_i03.encode(), "9bb2ec1293427054007fdabbedbe3afa0614c4748ee38526e9374dc152dfbcd5", 7624, "I03 runner")

    runner_i02 = _replace_once(
        runner_i03,
        "\n\ndef _relative(path: Path) -> str:\n    return str(path.resolve().relative_to(Path.cwd().resolve()))\n",
        "",
        "runner I02 relative helper",
    )
    runner_i02 = runner_i02.replace('{"path": _relative(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}', '{"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}')
    runner_i02_bytes = _expected(runner_i02.encode(), "7960692371e46ded13881d7da2e7cf09be7549247f43cd13f65b07158cfe8825", 7511, "I02 runner")

    math_i03 = math_current
    start = math_i03.index("\ndef m0_real_rule_oracle(")
    end = math_i03.index("\ndef compact_fixture_vector(", start)
    math_i03 = math_i03[:start] + math_i03[end:]
    math_i03 = _replace_once(math_i03, "    z_direct = partition_from_histogram(direct)\n", "    z_direct = partition_direct(all_rows)\n", "math I03 compact partition")
    math_i03_bytes = _expected(math_i03.encode(), "acbf0a0838c8299b35ab86daf63612404802f13085b2b3aeaf9def0096c939a9", 15089, "I03 math")

    math_i02 = _replace_once(
        math_i03,
        """\n\ndef partition_from_histogram(histogram: dict[int, int], *, scale: int = SCALE) -> Decimal:\n    \"\"\"Sum direct-enumeration multiplicities without repeating equal Decimal exponentials.\"\"\"\n    with localcontext() as context:\n        context.prec = DECIMAL_PRECISION\n        denominator = Decimal(scale)\n        return sum(Decimal(count) * (Decimal(score) / denominator).exp() for score, count in histogram.items())\n""",
        "",
        "math I02 partition helper",
    )
    math_i02 = _replace_once(math_i02, "        front_z_direct = partition_from_histogram(front_hist_direct)\n", "        front_z_direct = partition_direct(front_all)\n", "math I02 front partition")
    math_i02 = _replace_once(math_i02, "        back_z_direct = partition_from_histogram(back_hist_direct)\n", "        back_z_direct = partition_direct(back_all)\n", "math I02 back partition")
    math_i02_bytes = _expected(math_i02.encode(), "472573d73523b4bb2977a4b4e5d702c18a416d0790a4339af00c726c59ecaebd", 14644, "I02 math")

    finalize_i04 = finalize_current
    finalize_i04 = _replace_once(finalize_i04, '            {"attempt_id": "T10-I04", "status": "HOLD", "terminal": "HOLD_WORK_ITEM_RECEIPT", "disposition_path": str(PREP_ROOT / "work-items/T10-I04/attempt-disposition.json")},\n', "", "finalizer I04 history")
    finalize_i04 = _replace_once(finalize_i04, '        PREP_ROOT / "work-items/T10-I04/receipt.json",\n        PREP_ROOT / "work-items/T10-I04/attempt-disposition.json",\n', "", "finalizer I04 inputs")
    finalize_i04_bytes = _expected(finalize_i04.encode(), "49d51562e35a4725b8e23be84d608424968cdc8481fc81285481c7921309df7b", 13486, "I04 finalizer")

    targets = {
        "T10-I02": {
            "binding": PREP / "T10-I02/known-answers/known-answer-manifest.json",
            "files": {
                "scripts/phase4_independent/run_known_answers.py": runner_i02_bytes,
                "scripts/phase4_independent/oracle_math.py": math_i02_bytes,
            },
        },
        "T10-I03": {
            "binding": PREP / "T10-I03/known-answers/known-answer-manifest.json",
            "files": {
                "scripts/phase4_independent/run_known_answers.py": runner_i03_bytes,
                "scripts/phase4_independent/oracle_math.py": math_i03_bytes,
            },
        },
        "T10-I04": {
            "binding": PREP / "T10-I04/receipt.json",
            "files": {"scripts/phase4_independent/oracle_finalize_t10.py": finalize_i04_bytes},
        },
    }
    i05_receipt = _load(PREP / "T10/attempts/T10-I05/receipt.json")
    i05_expected = {row["path"]: row for row in i05_receipt["outputs"]}
    i05_paths = [
        "scripts/phase4_independent/oracle_math.py",
        "scripts/phase4_independent/oracle_metrics.py",
        "scripts/phase4_independent/run_known_answers.py",
        "scripts/phase4_independent/oracle_finalize_t10.py",
        "tests/phase4_oracle/test_oracle_math.py",
    ]
    i05_files: dict[str, bytes] = {}
    for relative in i05_paths:
        data = Path(relative).read_bytes()
        row = i05_expected[relative]
        _expected(data, row["sha256"], row["bytes"], f"I05 current snapshot {relative}")
        i05_files[relative] = data
    targets["T10-I05"] = {
        "binding": PREP / "T10/attempts/T10-I05/receipt.json",
        "files": i05_files,
    }
    return targets


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _base(attempt: str) -> Path:
    if attempt == "T10-I05":
        return PREP / "T10/attempts/T10-I05/preserved-shared-outputs"
    return PREP / f"{attempt}/preserved-shared-outputs"


def _map(attempt: str, payload: dict[str, Any]) -> tuple[Path, bytes, list[tuple[Path, bytes]]]:
    base = _base(attempt)
    writes: list[tuple[Path, bytes]] = []
    rows = []
    for original, data in sorted(payload["files"].items()):
        preserved = base / original
        writes.append((preserved, data))
        rows.append({
            "original_path": original,
            "preserved_path": str(preserved),
            "sha256": _sha_bytes(data),
            "bytes": len(data),
            "producer_actor_id": ACTOR,
            "task_id": "T10",
            "session_id": SESSION,
            "source_commit": SOURCE_COMMIT,
            "role": "independent_oracle_author",
        })
    binding = payload["binding"]
    mapping = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_preserved_shared_outputs_map",
        "attempt_id": attempt,
        "binding_path": str(binding),
        "binding_sha256": _sha_file(binding),
        "file_count": len(rows),
        "files": rows,
        "producer_provenance": {"producer_actor_id": ACTOR, "task_id": "T10", "session_id": SESSION, "source_commit": SOURCE_COMMIT, "role": "independent_oracle_author"},
        "status": "PRESERVED",
    }
    map_path = base / "preservation-map.json"
    map_bytes = _canonical(mapping)
    writes.append((map_path, map_bytes))
    return map_path, map_bytes, writes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    targets = _derive()
    all_writes: list[tuple[Path, bytes]] = []
    report = []
    for attempt, payload in sorted(targets.items()):
        map_path, map_bytes, writes = _map(attempt, payload)
        all_writes.extend(writes)
        report.append({"attempt_id": attempt, "file_count": len(payload["files"]), "map_path": str(map_path), "map_sha256": _sha_bytes(map_bytes)})
    duplicates = [path for path, _ in all_writes if path.exists()]
    if duplicates:
        raise ValueError(f"preservation targets already exist: {duplicates}")
    if args.write:
        for path, _ in all_writes:
            path.parent.mkdir(parents=True, exist_ok=True)
        for path, data in all_writes:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
    print(json.dumps({"status": "PASS", "mode": "write" if args.write else "dry-run", "attempts": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
