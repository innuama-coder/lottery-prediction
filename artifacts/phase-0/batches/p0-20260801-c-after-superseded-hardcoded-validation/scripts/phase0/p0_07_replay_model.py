"""Pure expected builders for the pre-attestation replay report."""

from __future__ import annotations

from typing import Any

from phase0lib import ValidationError, sha256_bytes


def aggregate_gates(per_game: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result=[]
    ids=[item["gate_id"] for item in per_game[0]["gate_results"]]
    for gate_id in ids:
        gates=[next(item for item in game["gate_results"] if item["gate_id"]==gate_id) for game in per_game]
        passed=all(item["outcome"]=="PASS" for item in gates)
        remediation="not_applicable" if passed else ("alternatives_exhausted_no_evidentiary_path" if any(item["remediation_status"]=="alternatives_exhausted_no_evidentiary_path" for item in gates) else "concrete_compliant_action_available")
        result.append({"gate_id":gate_id,"outcome":"PASS" if passed else "FAIL","remediation_status":remediation,"reason_code":f"global_{gate_id[2:].lower().replace('-','_')}_{'verified' if passed else 'failed'}","evidence_refs":sorted({ref for gate in gates for ref in gate["evidence_refs"]}),"reason":f"Global {gate_id} is the mechanical conjunction of DLT and SSQ results."})
    return result


def project_decision(per_game: list[dict[str, Any]]) -> str:
    outcomes=[item["technical_outcome"] for item in per_game]
    if outcomes.count("PASS_FULL")==2:return "GO"
    if any(item in {"PASS_FULL","PASS_LIMITED"} for item in outcomes):return "LIMITED_GO"
    if "HOLD" in outcomes:return "HOLD"
    if outcomes.count("STOP")==2:return "STOP"
    raise ValidationError("technical outcomes have no unique project decision")


def build_technical_replay_report(
    facts: dict[str, Any], *, started_at_utc: str, completed_at_utc: str,
    verification_command_sha256: str, consumer_receipt_bytes: bytes,
) -> dict[str, Any]:
    per_game=facts["per_game_results"]
    return {
        "schema_version":"1.0.0","artifact_type":"technical_replay_report","contract_version":"1.3",
        "executor_role":"pre_attestation_technical_replay_executor","report_scope":"technical_pre_attestation_first_12_gates",
        "started_at_utc":started_at_utc,"completed_at_utc":completed_at_utc,
        "verification_command_sha256":verification_command_sha256,"launcher_ref":"scripts/phase0/p0_07_replay_launcher.ps1",
        "input_manifest_sha256":facts["input_manifest_sha256"],
        "rebuilt_input_manifest_sha256":facts["rebuilt_input_manifest_sha256"],
        "rebuilt_output_manifest_sha256":facts["rebuilt_output_manifest_sha256"],
        "candidate_output_manifest_sha256":facts["candidate_output_manifest_sha256"],
        "gate_results":aggregate_gates(per_game),"per_game_results":per_game,"project_decision":project_decision(per_game),
        "deterministic_match":True,"input_unmodified":True,
        "proposed_handoff_file_bytes_sha256":facts["proposed_handoff_file_bytes_sha256"],
        "stage1_consumer_receipt_sha256":sha256_bytes(consumer_receipt_bytes),
    }


def validate_technical_replay_report(report:dict[str,Any],facts:dict[str,Any],**builder_args:Any)->None:
    expected=build_technical_replay_report(facts,**builder_args)
    if report!=expected: raise ValidationError("replay report differs from pure upstream-fact reconstruction")


# Transitional import aliases; all persisted pre-attestation bytes use the technical name.
build_replay_report = build_technical_replay_report
validate_replay_report = validate_technical_replay_report
