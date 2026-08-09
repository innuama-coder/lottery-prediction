from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lottery_research.phase3.work_items import create_prerun_work_item_receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("W01", "W02", "W03"), required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    parser.add_argument("--upstream-receipt", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = create_prerun_work_item_receipt(
        ROOT,
        args.check,
        args.identity,
        args.actor_assignments,
        args.output,
        args.upstream_receipt,
        command=sys.argv,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
