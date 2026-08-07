from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lottery_research.phase3.prerun_contract import bootstrap_prerun_contract


if __name__ == "__main__":
    receipt = bootstrap_prerun_contract(ROOT)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
