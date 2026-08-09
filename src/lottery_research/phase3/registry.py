from __future__ import annotations

from pathlib import Path
from typing import Any

from .serialization import load_json
from .serialization import sha256_file
from .schema import validate_payload


def load_and_validate_registries(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    model = load_json(root / "config/phase3/model-registry.json")
    feature = load_json(root / "config/phase3/feature-registry.json")
    validate_payload(root, "model_registry", model)
    validate_payload(root, "feature_registry", feature)
    source = root / model["implementation_identity"]["source_path"]
    if model["implementation_identity"]["source_sha256"] != sha256_file(source) or feature["implementation_identity"]["source_sha256"] != sha256_file(source):
        raise ValueError("registry implementation source identity mismatch")
    if model["implementation_identity"]["preregistration_sha256"] != sha256_file(root / "config/phase3/preregistration.json"):
        raise ValueError("model registry preregistration identity mismatch")
    if feature["implementation_identity"]["data_time_contract_sha256"] != sha256_file(root / "config/phase3/data-time-contract.json"):
        raise ValueError("feature registry data-time identity mismatch")
    if set(model["models"]) != {"M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7"}:
        raise ValueError("model registry coverage mismatch")
    if model["models"]["M0"]["role"] != "permanent_champion":
        raise ValueError("M0 permanent Champion identity changed")
    if model["models"]["M1"]["role"] != "mandatory_challenger":
        raise ValueError("M1 mandatory role changed")
    for model_id in ("M2", "M3", "M4"):
        row = model["models"][model_id]
        if row["opening_decision"] != "not_opened" or not row["opening_reason"]:
            raise ValueError(f"{model_id} lacks a result-before-opening decision")
    if model["models"]["M5"]["shadow_candidate_eligible"]:
        raise ValueError("M5 negative control cannot be shadow eligible")
    if model["models"]["M6"]["opening_decision"] != "prohibited":
        raise ValueError("M6 must remain prohibited")
    for feature_id, row in feature["features"].items():
        if feature_id != row["feature_id"]:
            raise ValueError("feature registry identity mismatch")
        if row["status"] == "eligible" and not row.get("availability_proof"):
            raise ValueError("eligible feature lacks availability proof")
    return model, feature
