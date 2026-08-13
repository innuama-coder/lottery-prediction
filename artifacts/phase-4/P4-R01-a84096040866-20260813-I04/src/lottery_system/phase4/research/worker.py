"""Scientific Phase 4 single-sequence black-box controller.

The worker is deliberately a stdin/stdout protocol boundary.  It imports the
accepted product controller only; it never imports an independent oracle,
power driver, reducer, or qualification script and never reads a result tree.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any

from ..serialization import canonical_json_bytes
from .controller import (
    ResearchControllerViolation,
    execute_registered_scientific_controller_fixture,
    execute_scientific_sequence_request,
)


class DuplicateRequestKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateRequestKey(f"duplicate scientific request key: {key}")
        value[key] = item
    return value


def _reject_float(raw: str) -> Decimal:
    raise ValueError(f"scientific request contains a JSON number with a fractional/exponent token: {raw}")


def reduce_one(raw: bytes) -> tuple[bytes, int]:
    try:
        if not raw or len(raw) > 1_000_000:
            raise ResearchControllerViolation("scientific controller request byte size is invalid")
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_float=_reject_float)
        if not isinstance(request, dict) or canonical_json_bytes(request) != raw:
            raise ResearchControllerViolation("scientific controller request is not canonical P4-CJSON-1")
        if request.get("artifact_type") == "phase4_registered_scientific_controller_test_request":
            result = execute_registered_scientific_controller_fixture(request)
        else:
            result = execute_scientific_sequence_request(request)
        code = 0
    except (DuplicateRequestKey, ResearchControllerViolation, UnicodeDecodeError, ValueError, KeyError) as exc:
        result = {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_scientific_controller_rejection",
            "status": "FAIL",
            "terminal": "SCIENTIFIC_CONTROLLER_INPUT_REJECTED",
            "guard_code": "SCIENTIFIC_CONTROLLER_CONTRACT_MISMATCH",
            "exit_code": 5,
            "error": str(exc),
        }
        code = 5
    return canonical_json_bytes(result), code


def main() -> int:
    if "--stream" in sys.argv[1:]:
        for line in sys.stdin.buffer:
            if not line.strip():
                continue
            result, code = reduce_one(line.rstrip(b"\n"))
            sys.stdout.buffer.write(result + b"\n")
            sys.stdout.buffer.flush()
            if code != 0:
                return code
        return 0
    result, code = reduce_one(sys.stdin.buffer.read())
    sys.stdout.buffer.write(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
