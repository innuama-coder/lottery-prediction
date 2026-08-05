from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_frozen_draws(root: Path, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Load the frozen Phase 1 baseline once and preserve its calendar order."""
    result: dict[str, list[dict[str, Any]]] = {game: [] for game in manifest["active_games"]}
    with (root / manifest["upstream"]["draws"]["path"]).open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            result[row["game"]].append(row)
    for rows in result.values():
        rows.sort(key=lambda row: (row["draw_date_local"], str(row["issue_id"])))
    return result
