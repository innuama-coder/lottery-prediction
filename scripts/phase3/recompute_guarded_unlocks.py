from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lottery_research.phase3.data_access import validate_guarded_unlock_evidence  # noqa: E402
from lottery_research.phase3.serialization import write_new_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Bottom-up recomputation of all Phase 3 guarded label unlocks.")
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_guarded_unlock_evidence(args.release_root.resolve())
    if args.output is not None:
        write_new_json(args.output.resolve(), result)
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if result["status"] == "PASS" else 5


if __name__ == "__main__":
    raise SystemExit(main())
