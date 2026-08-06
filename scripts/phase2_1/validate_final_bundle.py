from __future__ import annotations

import json

from bootstrap import activate

ROOT = activate()

from lottery_research.phase2_1 import RELEASE_ID
from lottery_research.phase2_1.workflow import bundle_path, validate_final_bundle

destination = bundle_path(ROOT)
acceptance = validate_final_bundle(ROOT, destination)
print(json.dumps({"release_id": RELEASE_ID, "command": "validate-final-bundle", "terminal": "PASS", "exit_code": 0, "evidence_hash_closure": acceptance["recomputed_metrics"]["evidence_hash_closure"]}, sort_keys=True))
