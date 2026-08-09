from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the immutable manifest plus allowed W12-W13 lifecycle outputs.")
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    release = args.release_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)

    manifest_path = release / "manifest/final-evidence-manifest.json"
    manifest = load(manifest_path)
    rows = manifest["files"]
    assert isinstance(rows, list)
    blockers: list[str] = []
    listed: set[str] = set()
    listed_bytes = 0
    for row in rows:
        assert isinstance(row, dict)
        relative = str(row["path"])
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts or "*" in relative or "latest" in relative.lower():
            blockers.append(f"UNSAFE:{relative}")
        if relative in listed:
            blockers.append(f"DUPLICATE:{relative}")
        listed.add(relative)
        path = release / relative
        if not path.is_file():
            blockers.append(f"MISSING:{relative}")
            continue
        size = path.stat().st_size
        listed_bytes += size
        if size != row["bytes"]:
            blockers.append(f"BYTES:{relative}")
        if file_sha256(path) != row["sha256"]:
            blockers.append(f"SHA256:{relative}")
        if len(path.read_bytes().splitlines()) != row["lines"]:
            blockers.append(f"LINES:{relative}")
    if hashlib.sha256(canonical_bytes(rows)).hexdigest() != manifest["inventory_sha256"]:
        blockers.append("INVENTORY_DIGEST")

    actual = {path.relative_to(release).as_posix() for path in release.rglob("*") if path.is_file()}
    extras = actual - listed
    allowed_exact = {
        "manifest/final-evidence-manifest.json",
        "work-items/W12/receipt.json",
        "work-items/W13/receipt.json",
    }
    allowed_prefixes = ("acceptance/", "handoff/", "handoff-validation/")
    disallowed_extras = sorted(
        relative for relative in extras
        if relative not in allowed_exact and not relative.startswith(allowed_prefixes)
    )
    if disallowed_extras:
        blockers.extend(f"DISALLOWED_EXTRA:{relative}" for relative in disallowed_extras)

    acceptance_paths = sorted(release.glob("acceptance/*/acceptance.json"))
    if len(acceptance_paths) != 1:
        blockers.append(f"ACCEPTANCE_COUNT:{len(acceptance_paths)}")
        acceptance: dict[str, object] = {}
    else:
        acceptance = load(acceptance_paths[0])
        if acceptance.get("status") != "PASS" or acceptance.get("delivery_status") != "GO":
            blockers.append("ACCEPTANCE_TERMINAL")
        if acceptance.get("manifest_sha256") != file_sha256(manifest_path):
            blockers.append("ACCEPTANCE_MANIFEST_BINDING")

    handoff = load(release / "handoff/handoff.json")
    if acceptance_paths and handoff.get("acceptance_sha256") != file_sha256(acceptance_paths[0]):
        blockers.append("HANDOFF_ACCEPTANCE_BINDING")
    if handoff.get("manifest_sha256") != file_sha256(manifest_path):
        blockers.append("HANDOFF_MANIFEST_BINDING")
    if handoff.get("status") != "PASS" or handoff.get("delivery_status") != "GO":
        blockers.append("HANDOFF_TERMINAL")

    frozen_checks = load(release / "handoff-validation/frozen-acceptance-checks-through-W13-I02/summary.json")
    if frozen_checks.get("status") != "PASS" or frozen_checks.get("command_count") != 18:
        blockers.append("FROZEN_COMMAND_SET")
    for ordinal in range(1, 14):
        receipt = load(release / f"work-items/W{ordinal:02d}/receipt.json") if ordinal >= 7 else None
        if receipt is not None and (
            receipt.get("status") != "PASS"
            or receipt.get("process_exit_code") != 0
            or receipt.get("work_item") != f"W{ordinal:02d}"
        ):
            blockers.append(f"RECEIPT_W{ordinal:02d}")

    validator = load(release / "acceptance/iteration-01/validator.json")
    final_revalidation = load(release / "handoff-validation/final-revalidation/final-validation.json")
    handoff_validation = load(release / "handoff-validation/handoff-validation.json")
    for name, artifact in (
        ("ACCEPTANCE_VALIDATOR", validator),
        ("FINAL_REVALIDATION", final_revalidation),
        ("HANDOFF_VALIDATION", handoff_validation),
    ):
        if artifact.get("status") != "PASS" or artifact.get("blocking_findings") not in (0, []):
            blockers.append(name)

    report = {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_final_lifecycle_postcheck",
        "release_id": release.name,
        "status": "PASS" if not blockers else "HOLD",
        "terminal": "PASS" if not blockers else "HOLD",
        "blocking_findings": len(blockers),
        "blockers": blockers,
        "manifest": {
            "path": "manifest/final-evidence-manifest.json",
            "sha256": file_sha256(manifest_path),
            "inventory_sha256": manifest["inventory_sha256"],
            "listed_file_count": len(rows),
            "listed_total_bytes": listed_bytes,
            "missing_count": sum(item.startswith("MISSING:") for item in blockers),
            "duplicate_count": sum(item.startswith("DUPLICATE:") for item in blockers),
            "unsafe_count": sum(item.startswith("UNSAFE:") for item in blockers),
            "hash_mismatch_count": sum(item.startswith(("SHA256:", "BYTES:", "LINES:")) for item in blockers),
            "disallowed_extra_count": len(disallowed_extras),
            "allowed_post_manifest_extra_count": len(extras) - len(disallowed_extras),
        },
        "acceptance": {
            "count": len(acceptance_paths),
            "path": acceptance_paths[0].relative_to(release).as_posix() if len(acceptance_paths) == 1 else None,
            "sha256": file_sha256(acceptance_paths[0]) if len(acceptance_paths) == 1 else None,
            "status": acceptance.get("status"),
            "delivery_status": acceptance.get("delivery_status"),
            "scientific_summary": acceptance.get("scientific_summary"),
        },
        "frozen_acceptance_command_count": frozen_checks.get("command_count"),
        "w01_w13_receipt_validation": frozen_checks.get("status"),
        "m0_permanent_champion": handoff.get("m0_permanent_champion"),
        "forbidden_actions_authorized": handoff.get("forbidden_actions_authorized"),
    }
    output.write_bytes(canonical_bytes(report))
    print(json.dumps(report, sort_keys=True))
    return 0 if not blockers else 5


if __name__ == "__main__":
    raise SystemExit(main())
