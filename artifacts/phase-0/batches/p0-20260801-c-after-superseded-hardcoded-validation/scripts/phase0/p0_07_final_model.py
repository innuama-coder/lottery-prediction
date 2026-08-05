"""Pure expected-byte builders for attested Phase 0 terminal artifacts."""

from __future__ import annotations

import hashlib
from typing import Any

from p0_07_decision import derive_per_game_outcome, derive_project_decision
from p0_07_replay_model import aggregate_gates
from phase0lib import canonical_json_bytes, sha256_bytes


def _ref(path:str,payload:bytes)->str:
    return f"{path}#sha256={sha256_bytes(payload)}"


def build_final_replay(
    technical:dict[str,Any], *, execution_bytes:bytes, consumer_bytes:bytes, fixture_bytes:bytes,
    reviewer_receipt_bytes:bytes, attestation_bytes:bytes,
)->dict[str,Any]:
    receipt_ref=_ref("artifacts/phase-0/p0-07-reviewer-verification-receipt.json",reviewer_receipt_bytes)
    attestation_ref=_ref("artifacts/phase-0/reviewer-attestation.json",attestation_bytes)
    consumer_ref=_ref("artifacts/phase-0/p0-07-review-bundle/p0-07-stage1-consumer-receipt.json",consumer_bytes)
    fixture_ref=_ref("artifacts/phase-0/stage1-handoff-fixture.json",fixture_bytes)
    per_game=[]
    for source in technical["per_game_results"]:
        gates=[dict(item) for item in source["gate_results"]]
        gates.extend([
            {"gate_id":"G-REPRODUCIBILITY","outcome":"PASS","remediation_status":"not_applicable","reason_code":"independent_reviewer_verification_passed","evidence_refs":[receipt_ref,attestation_ref],"reason":f"{source['game']} independent technical verification and declared reviewer attestation passed."},
            {"gate_id":"G-HANDOFF","outcome":"PASS","remediation_status":"not_applicable","reason_code":"external_stage1_consumer_passed","evidence_refs":[consumer_ref,fixture_ref],"reason":f"{source['game']} exact proposed fixture bytes were accepted by the external Stage 1 consumer."},
        ])
        outcome=derive_per_game_outcome(gates,source["coverage_tier"])
        if outcome!=source["technical_outcome"]: raise ValueError("terminal gates changed technical per-game outcome")
        per_game.append({"game":source["game"],"gate_results":gates,"per_game_outcome":outcome,"coverage_tier":source["coverage_tier"]})
    project=derive_project_decision(per_game)
    return {
        "schema_version":"2.0.0","artifact_type":"replay_report","contract_version":"1.3","report_scope":"attested_final_fourteen_gates",
        "technical_report_sha256":sha256_bytes(canonical_json_bytes(technical)+b"\n"),
        "reviewer_verification_receipt_sha256":sha256_bytes(reviewer_receipt_bytes),"reviewer_attestation_sha256":sha256_bytes(attestation_bytes),
        "execution_receipt_sha256":sha256_bytes(execution_bytes),"stage1_consumer_receipt_sha256":sha256_bytes(consumer_bytes),
        "final_fixture_file_bytes_sha256":sha256_bytes(fixture_bytes),"gate_results":aggregate_gates(per_game),
        "per_game_results":per_game,"project_decision":project,"deterministic_derivation":True,
    }


def build_machine_decision(final_replay:dict[str,Any],final_replay_bytes:bytes,fixture_bytes:bytes,attestation_bytes:bytes)->dict[str,Any]:
    return {"schema_version":"1.0.0","artifact_type":"machine_acceptance_decision","contract_version":"1.3",
            "decision":final_replay["project_decision"],"game_outcomes":[{"game":x["game"],"outcome":x["per_game_outcome"]} for x in final_replay["per_game_results"]],
            "final_replay_report_sha256":sha256_bytes(final_replay_bytes),"final_fixture_file_bytes_sha256":sha256_bytes(fixture_bytes),
            "reviewer_attestation_sha256":sha256_bytes(attestation_bytes),"derivation":"contract_v1_3_ordered_mechanical"}


def build_acceptance_markdown(decision:dict[str,Any])->bytes:
    games="\n".join(f"- {item['game'].upper()}: {item['outcome']}" for item in decision["game_outcomes"])
    text=("# Phase 0 Acceptance Report\n\n"
          f"Decision: {decision['decision']}\n\n"
          f"{games}\n\n"
          f"Final replay SHA-256: `{decision['final_replay_report_sha256']}`\n\n"
          f"Final fixture bytes SHA-256: `{decision['final_fixture_file_bytes_sha256']}`\n\n"
          "This report is a deterministic rendering of machine-acceptance-decision.json.\n")
    return text.encode("utf-8")


def canonical_line(value:dict[str,Any])->bytes:
    return canonical_json_bytes(value)+b"\n"


def signature_payload(attestation:dict[str,Any])->bytes:
    unsigned={key:value for key,value in attestation.items() if key!="signature"}
    return canonical_json_bytes(unsigned)
