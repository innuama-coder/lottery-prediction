from __future__ import annotations

import json

from bootstrap import activate

ROOT = activate()

from lottery_research.phase2_1 import RELEASE_ID
from lottery_research.phase2_1.schema import validate
from lottery_research.phase2_1.serialization import load_json
from lottery_research.phase2_1.workflow import bundle_path, verify_evidence_manifest

destination = bundle_path(ROOT)
acceptance = load_json(destination / "acceptance/acceptance.json")
validate("acceptance", acceptance)
closure = verify_evidence_manifest(destination, load_json(destination / "acceptance/manifest.json"))
if acceptance["release_id"] != RELEASE_ID or acceptance["status"] != "PASS" or closure != 1.0:
    raise SystemExit(5)
print(json.dumps({"release_id": RELEASE_ID, "command": "validate-final-bundle", "terminal": "PASS", "exit_code": 0, "evidence_hash_closure": closure}, sort_keys=True))
