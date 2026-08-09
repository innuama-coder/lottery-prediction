from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lottery_research.phase3.work_items import validate_work_item_receipt_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    parser.add_argument("--expected-work-item", required=True, choices=tuple(f"W{index:02d}" for index in range(1, 14)))
    args = parser.parse_args()
    result = validate_work_item_receipt_file(ROOT, args.receipt, args.actor_assignments, args.expected_work_item)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
