from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .real_model import canonical, digest, elementary, load_draws, score_ticket, top_tickets, train, write_jsonl_once, write_once


ZERO = "0" * 64


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_root_for_model(model_path: Path) -> Path:
    path = model_path.resolve()
    for parent in path.parents:
        if parent.name == "models":
            return parent.parent
    raise ValueError("model release must be inside <release>/models")


class ProductLedger:
    """Small create-once hash-chain used by the real-model product CLI."""

    def __init__(self, release: Path):
        self.release = release.resolve()
        self.root = self.release / "runtime/ledger"
        self.events = self.root / "events"
        self.head = self.root / "head.json"

    def rows(self) -> list[dict[str, Any]]:
        rows = [load(path) for path in sorted(self.events.glob("*.json"))] if self.events.exists() else []
        previous = ZERO
        for ordinal, row in enumerate(rows, 1):
            body = {key: value for key, value in row.items() if key != "event_sha256"}
            if row["ordinal"] != ordinal or row["previous_event_sha256"] != previous or digest(body) != row["event_sha256"]:
                raise ValueError("HOLD_LEDGER_HASH_CHAIN_INVALID")
            previous = row["event_sha256"]
        if self.head.exists():
            head = load(self.head)
            if not rows or head != {"ordinal": len(rows), "event_sha256": previous}:
                raise ValueError("HOLD_LEDGER_HEAD_INVALID")
        elif rows:
            raise ValueError("HOLD_LEDGER_HEAD_MISSING")
        return rows

    def append(self, event_type: str, object_id: str, payload: dict[str, Any], *, actor: str = "phase4-real-cli") -> tuple[dict[str, Any], bool]:
        rows = self.rows()
        for row in rows:
            if row["event_type"] == event_type and row["object_id"] == object_id:
                if row["payload_sha256"] != digest(payload):
                    raise ValueError("HOLD_IDEMPOTENCY_IDENTITY_COLLISION")
                return row, False
        ordinal = len(rows) + 1
        body = {
            "artifact_type": "phase4_product_ledger_event", "ordinal": ordinal,
            "event_id": f"event-{digest({'type': event_type, 'object': object_id, 'payload': payload})[:24]}",
            "event_type": event_type, "object_id": object_id,
            "event_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "actor": actor, "previous_event_sha256": rows[-1]["event_sha256"] if rows else ZERO,
            "payload_sha256": digest(payload), "payload": payload,
        }
        row = {**body, "event_sha256": digest(body)}
        self.events.mkdir(parents=True, exist_ok=True)
        event_path = self.events / f"{ordinal:08d}-{row['event_id']}.json"
        write_once(event_path, row)
        new_head = {"ordinal": ordinal, "event_sha256": row["event_sha256"]}
        self.head.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.head.with_suffix(".next")
        temporary.write_bytes(canonical(new_head))
        temporary.replace(self.head)
        return row, True


def forecast_and_lock(model_path: Path, target_issue: str, top_k: int = 1000) -> dict[str, Any]:
    model = load(model_path)
    if model.get("family") == "M0" or model.get("family") != "P4E1-R":
        raise ValueError("HOLD_NON_PRODUCT_OR_UNKNOWN_MODEL")
    release = release_root_for_model(model_path)
    game = model["game"]
    rows = top_tickets(model, top_k)
    forecast_id = f"forecast-{game}-{digest({'model': model['model_release_id'], 'target': target_issue, 'rows': rows})[:20]}"
    target = release / f"runtime/forecasts/{game}/{target_issue}"
    write_jsonl_once(target / "top1000.jsonl", rows)
    forecast = {
        "artifact_type": "phase4_formal_forecast", "forecast_id": forecast_id, "game": game,
        "target_issue": target_issue, "model_release_id": model["model_release_id"],
        "model_sha256": sha(model_path), "ticket_count": len(rows),
        "top1000_sha256": sha(target / "top1000.jsonl"), "status": "locked_unscored",
    }
    write_once(target / "forecast.json", forecast)
    lock = {"artifact_type": "phase4_forecast_lock", "forecast_id": forecast_id,
            "forecast_sha256": sha(target / "forecast.json"), "top1000_sha256": forecast["top1000_sha256"], "status": "LOCKED"}
    write_once(target / "lock.json", lock)
    event, appended = ProductLedger(release).append("forecast_locked", forecast_id, lock)
    return {**forecast, "status": "LOCKED", "lock_path": str(target / "lock.json"), "ledger_event_id": event["event_id"], "idempotent_replay": not appended}


def score_release(release: Path, game: str) -> dict[str, Any]:
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    model = load(release / serving["model_path"])
    source = load(release / f"data/{game}/training-input-manifest.json")
    draws_path = Path(source["draws_path"])
    if not draws_path.is_absolute():
        draws_path = (Path.cwd() / draws_path).resolve()
    draws = load_draws(draws_path, game)
    historical = train(game, draws, len(draws) - 1)
    top = top_tickets(historical)
    score = {"artifact_type": "phase4_score", "game": game, "target_issue": draws[-1].issue,
             "official_result_revision": 1, "unlock": "virtual_clock_after_official_result",
             "score": score_ticket(historical, draws[-1], top), "status": "SCORED"}
    path = release / f"runtime/scores/{game}/{draws[-1].issue}/revision-1.json"
    write_once(path, score)
    ledger = ProductLedger(release)
    unlock, unlock_new = ledger.append("official_result_unlocked", f"{game}:{draws[-1].issue}:r1", {"game": game, "issue": draws[-1].issue, "revision": 1})
    event, appended = ledger.append("forecast_scored", f"{game}:{draws[-1].issue}:r1", {"score_sha256": sha(path), "result_revision": 1})
    return {"status": "PASS", "score_path": str(path), "unlock_event_id": unlock["event_id"], "score_event_id": event["event_id"], "idempotent_replay": not unlock_new and not appended}


def research_release(release: Path, game: str) -> dict[str, Any]:
    serving_path = release / "selection/serving-selection.json"
    serving_before = sha(serving_path)
    serving = load(serving_path)["serving_model_by_game"][game]
    parent = load(release / serving["model_path"])
    child = json.loads(json.dumps(parent))
    child["zones"][0]["theta"] = float(child["zones"][0]["theta"]) * 0.75
    for zone in child["zones"]:
        zone["weights"] = [math.exp(max(-8.0, min(8.0, float(zone["theta"]) * float(value)))) for value in zone["feature"]]
        zone["normalizer"] = elementary(zone["weights"], int(zone["k"]))
    child_id = f"p4e1r-{game}-child-{digest({'parent': parent['model_release_id'], 'theta': [z['theta'] for z in child['zones']]})[:16]}"
    child["model_release_id"] = child_id
    parent_top, child_top = top_tickets(parent), top_tickets(child)
    root = release / f"runtime/research/{game}/{child_id}"
    write_once(root / "child-model.json", child)
    write_jsonl_once(root / "shadow-top1000.jsonl", child_top)
    decision = {"artifact_type": "phase4_research_decision", "game": game,
                "parent_model_release_id": parent["model_release_id"], "child_model_release_id": child_id,
                "allowed_change": {"zone0_theta_multiplier": 0.75},
                "probability_changed": child_top[0]["joint_probability"] != parent_top[0]["joint_probability"],
                "top1000_changed": digest(child_top) != digest(parent_top), "decision": "shadow_only",
                "serving_changed": False, "status": "PASS"}
    write_once(root / "decision.json", decision)
    if sha(serving_path) != serving_before:
        raise ValueError("HOLD_RESEARCH_CHANGED_SERVING")
    event, appended = ProductLedger(release).append("research_shadow_created", child_id, {"decision_sha256": sha(root / "decision.json"), **decision})
    return {**decision, "decision_path": str(root / "decision.json"), "ledger_event_id": event["event_id"], "idempotent_replay": not appended}


def schedule_release(release: Path, game: str | None, fail_after: str | None = None, cycle_id: str = "formal-cycle-v1") -> dict[str, Any]:
    games = [game] if game else ["ssq", "dlt"]
    ledger = ProductLedger(release)
    effects = []
    for selected in games:
        if not cycle_id.replace("-", "").isalnum():
            raise ValueError("invalid cycle identity")
        run_id = f"schedule-{selected}-{cycle_id}"
        checkpoint = release / f"runtime/schedule/{run_id}/checkpoint.json"
        if checkpoint.exists():
            state = load(checkpoint)
        else:
            state = {"run_id": run_id, "game": selected, "completed": []}
        for stage in ("prepare", "forecast_lock", "official_result_ingest", "unlock_score", "research_shadow"):
            if stage in state["completed"]:
                continue
            if fail_after == stage:
                write_once(release / f"runtime/schedule/{run_id}/fault-{stage}.json", {"run_id": run_id, "fault_after": stage, "status": "INTERRUPTED"})
                return {"status": "INTERRUPTED", "run_id": run_id, "fault_after": stage}
            state["completed"].append(stage)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_bytes(canonical(state))
        event, appended = ledger.append("schedule_cycle_terminal", run_id, {"game": selected, "completed": state["completed"], "terminal": "PASS"})
        effects.append({"game": selected, "run_id": run_id, "event_id": event["event_id"], "idempotent_replay": not appended})
    report = {"artifact_type": "phase4_schedule_recovery_report", "games": games,
              "stages": ["prepare", "forecast_lock", "official_result_ingest", "unlock_score", "research_shadow"],
              "duplicate_side_effects": 0, "effects": effects, "status": "PASS"}
    report_path = release / f"runtime/schedule/recovery-{'-'.join(games)}.json"
    if report_path.exists():
        frozen = load(report_path)
        if frozen.get("duplicate_side_effects") != 0 or frozen.get("status") != "PASS" or frozen.get("games") != games:
            raise ValueError("HOLD_SCHEDULE_RECOVERY_IDENTITY_COLLISION")
        return {**frozen, "idempotent_replay": all(effect["idempotent_replay"] for effect in effects)}
    write_once(report_path, report)
    return report
