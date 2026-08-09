from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_sha(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    release = args.release_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)

    blockers: list[str] = []

    def check(condition: bool, code: str) -> None:
        if not condition:
            blockers.append(code)

    manifest_path = release / "manifest/final-evidence-manifest.json"
    manifest = load(manifest_path)
    rows = manifest["files"]
    assert isinstance(rows, list)
    listed: set[str] = set()
    total_bytes = 0
    for row in rows:
        assert isinstance(row, dict)
        relative = str(row["path"])
        safe = "latest" not in relative.lower() and "*" not in relative and ".." not in Path(relative).parts and not Path(relative).is_absolute()
        check(safe, f"UNSAFE:{relative}")
        check(relative not in listed, f"DUPLICATE:{relative}")
        listed.add(relative)
        path = release / relative
        check(path.is_file(), f"MISSING:{relative}")
        if path.is_file():
            check(path.stat().st_size == row["bytes"], f"BYTES:{relative}")
            check(file_sha(path) == row["sha256"], f"SHA256:{relative}")
            check(len(path.read_bytes().splitlines()) == row["lines"], f"LINES:{relative}")
            total_bytes += path.stat().st_size
    check(canonical_sha(rows) == manifest["inventory_sha256"], "INVENTORY_DIGEST")

    actual = {path.relative_to(release).as_posix() for path in release.rglob("*") if path.is_file()}
    allowed_exact = {"manifest/final-evidence-manifest.json", "work-items/W12/receipt.json"}
    extras = actual - listed
    disallowed_extras = sorted(relative for relative in extras if relative not in allowed_exact and not relative.startswith("acceptance/iteration-01/"))
    check(not disallowed_extras, "DISALLOWED_EXTRAS:" + ",".join(disallowed_extras))

    acceptance_paths = sorted(release.glob("acceptance/*/acceptance.json"))
    check(len(acceptance_paths) == 1, "UNIQUE_ACCEPTANCE")
    acceptance_path = acceptance_paths[0]
    acceptance = load(acceptance_path)
    validator = load(acceptance_path.parent / "validator.json")
    receipt = load(release / "work-items/W12/receipt.json")
    report_json = load(release / "reports/final-research-report.json")
    report_md = (release / "reports/final-research-report.md").read_text(encoding="utf-8").lower()
    check(acceptance["status"] == "PASS" and acceptance["delivery_status"] == "GO" and acceptance["blocking_findings"] == 0, "ACCEPTANCE_TERMINAL")
    check(acceptance["manifest_sha256"] == file_sha(manifest_path), "ACCEPTANCE_MANIFEST_BINDING")
    check(acceptance["classification_approver_id"] == "P3-W12-CLASSIFICATION-APPROVER-20260809-R02", "ACCEPTANCE_APPROVER")
    check(acceptance["approver_task_id"] == "/root/w12_r02_approver" and acceptance["approver_session_id"] == "P3-W12-CLASSIFICATION-APPROVAL-SESSION-20260809-R02-8E64A17C", "ACCEPTANCE_PROVENANCE")
    check(acceptance["scientific_summary"] == "no_shadow_candidate" and acceptance["m0_permanent_champion"] is True, "ACCEPTANCE_SCIENCE")
    check(validator["status"] == "PASS" and validator["terminal"] == "PASS" and validator["delivery_status"] == "GO", "VALIDATOR_TERMINAL")
    check(validator["blocking_findings"] == 0 and validator["hash_match_rate"] == validator["delivery_coverage"] == validator["e2e_coverage"] == 1.0, "VALIDATOR_COVERAGE")
    check(validator["self_reported_fields_trusted"] == 0 and validator["champion_change_count"] == 0 and validator["forbidden_action_count"] == 0, "VALIDATOR_BOTTOM_UP")
    check(receipt["status"] == "PASS" and receipt["terminal"] == "PASS" and receipt["process_exit_code"] == 0 and receipt["owner_id"] == acceptance["classification_approver_id"], "W12_RECEIPT")
    check(report_json["delivery_status"] == "GO" and report_json["scientific_summary"] == "no_shadow_candidate", "REPORT_CONCLUSIONS")
    check(report_json["model_classifications"] == {"M1": "archived", "M2": "not_opened", "M3": "not_opened", "M4": "not_opened"}, "REPORT_CLASSIFICATION")
    check(report_json["m0_permanent_champion"] is True and report_json["forbidden_actions_authorized"] == [], "REPORT_AUTHORIZATION")
    language = report_json["scientific_language"]
    check("not evidence of real future advantage" in language["historical_only"].lower(), "REPORT_FUTURE_BOUNDARY")
    check("does not prove randomness" in language["indeterminate"].lower(), "REPORT_RANDOMNESS_BOUNDARY")
    check("no production" in language["authorization"].lower() and "betting" in language["authorization"].lower(), "REPORT_PRODUCTION_BOUNDARY")
    check("retrospective historical research only" in report_md and "does not establish a real future advantage" in report_md, "MARKDOWN_HISTORICAL_BOUNDARY")
    check("does not prove lottery randomness" in report_md and "grants no production" in report_md and "betting" in report_md, "MARKDOWN_AUTHORIZATION_BOUNDARY")

    report = {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_w12_manifest_postcheck",
        "identity": "P3-R02-14df4f3-20260809-I01-W12-I01-POSTCHECK",
        "release_id": release.name,
        "status": "PASS" if not blockers else "HOLD",
        "terminal": "PASS" if not blockers else "HOLD",
        "blocking_findings": len(blockers),
        "blockers": blockers,
        "manifest": {
            "path": "manifest/final-evidence-manifest.json",
            "sha256": file_sha(manifest_path),
            "listed_file_count": len(listed),
            "listed_total_bytes": total_bytes,
            "inventory_sha256": manifest["inventory_sha256"],
            "hash_match_rate": 1.0 if not any(code.startswith(("SHA256:", "BYTES:", "MISSING:")) for code in blockers) else 0.0,
            "missing_count": sum(code.startswith("MISSING:") for code in blockers),
            "duplicate_count": sum(code.startswith("DUPLICATE:") for code in blockers),
            "unsafe_count": sum(code.startswith("UNSAFE:") for code in blockers),
            "disallowed_extra_count": len(disallowed_extras),
            "allowed_unlisted_paths": sorted(extras),
        },
        "acceptance": {
            "path": acceptance_path.relative_to(release).as_posix(),
            "sha256": file_sha(acceptance_path),
            "unique_count": len(acceptance_paths),
            "status": acceptance["status"],
            "delivery_status": acceptance["delivery_status"],
            "scientific_summary": acceptance["scientific_summary"],
        },
        "w12_receipt_sha256": file_sha(release / "work-items/W12/receipt.json"),
        "validator_sha256": file_sha(acceptance_path.parent / "validator.json"),
        "final_report_json_sha256": file_sha(release / "reports/final-research-report.json"),
        "final_report_markdown_sha256": file_sha(release / "reports/final-research-report.md"),
        "m0_permanent_champion": True,
        "champion_change_count": 0,
        "network_request_count": 0,
        "wording_boundary": "PASS" if not any("BOUNDARY" in code for code in blockers) else "HOLD",
    }
    output.write_bytes(canonical_bytes(report))
    print(json.dumps({"status": report["status"], "blocking_findings": len(blockers), "manifest_sha256": report["manifest"]["sha256"], "listed_file_count": len(listed), "listed_total_bytes": total_bytes, "acceptance_sha256": report["acceptance"]["sha256"]}, sort_keys=True))
    return 0 if not blockers else 5


if __name__ == "__main__":
    raise SystemExit(main())
