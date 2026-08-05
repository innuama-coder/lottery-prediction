from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .models import ContractViolation
from .serialization import canonical_json_bytes
from .steps.preflight import BootstrapArguments, IncrementalArguments, PreflightError
from .workflow import classify_failure, execute_bootstrap, execute_incremental, execute_replay, execute_verify


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PreflightError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="lottery-data", add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--mode", required=True)
    run.add_argument("--source-mode", required=True)
    run.add_argument("--phase0-snapshot", type=Path)
    run.add_argument("--snapshot-root", type=Path)
    run.add_argument("--run-id")
    run.add_argument("--release-id")
    run.add_argument("--artifacts-root", type=Path, required=True)
    run.add_argument("--config-root", type=Path)
    run.add_argument("--games", default="ssq,dlt")
    replay = commands.add_parser("replay")
    replay.add_argument("--source-run-id", required=True)
    replay.add_argument("--run-id")
    replay.add_argument("--offline", action="store_true")
    replay.add_argument("--artifacts-root", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--release-id", required=True)
    verify.add_argument("--artifacts-root", type=Path, required=True)
    verify.add_argument("--snapshot-root", type=Path)
    return parser


def _preflight_result(message: str, exit_code: int = 4, *, mode: str = "bootstrap") -> dict[str, object]:
    result: dict[str, object] = {
        "preflight_result_schema_version": "1.0.0",
        "mode": mode if mode in {"bootstrap", "incremental", "replay", "verify"} else "bootstrap",
        "status": "rejected",
        "request_stats": {"planned": 0, "started": 0, "succeeded": 0, "failed": 0, "not_started": 0},
        "exit_code": exit_code,
        "message": message,
    }
    return result


def _execution_failure_result(message: str, exit_code: int, *, mode: str) -> dict[str, object]:
    return {
        "execution_failure_schema_version": "1.0.0",
        "mode": mode if mode in {"bootstrap", "incremental", "replay", "verify"} else "bootstrap",
        "status": "interrupted", "exit_code": exit_code, "message": message,
    }


def _run_artifact_exists(artifacts_root: Path | None, run_id: str | None) -> bool:
    if artifacts_root is None or run_id is None or not run_id or not all(
        character.isalnum() or character in "._-" for character in run_id
    ):
        return False
    return any(path.exists() for path in (
        artifacts_root / "runs" / run_id,
        artifacts_root / ".run-recovery" / run_id,
        artifacts_root / f".run-lock-{run_id}.lock",
    ))


def _auto_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"phase1-{kind}-{stamp}"


def main(argv: Sequence[str] | None = None) -> int:
    mode_for_error = "bootstrap"
    artifacts_for_error: Path | None = None
    run_id_for_error: str | None = None
    try:
        namespace = build_parser().parse_args(list(argv) if argv is not None else None)
        mode_for_error = getattr(namespace, "mode", namespace.command)
        artifacts_for_error = getattr(namespace, "artifacts_root", None)
        if namespace.command == "verify":
            code, result = execute_verify(
                artifacts_root=namespace.artifacts_root, release_id=namespace.release_id,
                snapshot_root_override=namespace.snapshot_root,
            )
        elif namespace.command == "replay":
            if not namespace.offline:
                raise PreflightError("replay requires --offline")
            code, result = execute_replay(
                artifacts_root=namespace.artifacts_root, source_run_id=namespace.source_run_id,
                run_id=(run_id_for_error := (namespace.run_id or _auto_id("replay"))), offline=True,
            )
        else:
            games = tuple(value.strip() for value in namespace.games.split(",") if value.strip())
            if namespace.mode == "bootstrap":
                if namespace.phase0_snapshot is None or namespace.release_id is None or namespace.run_id is None or namespace.snapshot_root is not None:
                    raise PreflightError("bootstrap requires --phase0-snapshot, --run-id and --release-id, and forbids --snapshot-root")
                arguments = BootstrapArguments(
                    mode=namespace.mode, source_mode=namespace.source_mode,
                    phase0_snapshot=namespace.phase0_snapshot, artifacts_root=namespace.artifacts_root,
                    config_root=namespace.config_root, run_id=namespace.run_id, release_id=namespace.release_id,
                    games=games,
                )
                run_id_for_error = namespace.run_id
                code, result = execute_bootstrap(arguments)
            elif namespace.mode == "incremental":
                if namespace.phase0_snapshot is not None:
                    raise PreflightError("incremental forbids --phase0-snapshot")
                if namespace.source_mode == "snapshot" and namespace.snapshot_root is None:
                    raise PreflightError("snapshot incremental requires --snapshot-root")
                if namespace.source_mode == "live" and namespace.snapshot_root is not None:
                    raise PreflightError("live incremental forbids --snapshot-root")
                if namespace.source_mode not in {"snapshot", "live"}:
                    raise PreflightError("incremental source-mode must be snapshot or live")
                run_id = namespace.run_id or _auto_id("run")
                run_id_for_error = run_id
                release_id = namespace.release_id or _auto_id("release")
                arguments = IncrementalArguments(
                    mode=namespace.mode, source_mode=namespace.source_mode,
                    snapshot_root=namespace.snapshot_root, artifacts_root=namespace.artifacts_root,
                    config_root=namespace.config_root, run_id=run_id, release_id=release_id, games=games,
                )
                code, result = execute_incremental(arguments)
            else:
                raise PreflightError(f"unsupported run mode: {namespace.mode!r}")
    except PreflightError as exc:
        code = 4
        result = (
            _execution_failure_result(str(exc), code, mode=mode_for_error)
            if _run_artifact_exists(artifacts_for_error, run_id_for_error)
            else _preflight_result(str(exc), mode=mode_for_error)
        )
        print(str(exc), file=sys.stderr)
    except ContractViolation as exc:
        failure = classify_failure(exc)
        code, result = failure.exit_code, _preflight_result(str(failure), failure.exit_code, mode=mode_for_error)
        print(str(failure), file=sys.stderr)
    except Exception as exc:
        failure = classify_failure(exc)
        result = (
            _execution_failure_result(str(failure), failure.exit_code, mode=mode_for_error)
            if _run_artifact_exists(artifacts_for_error, run_id_for_error)
            else _preflight_result(str(failure), failure.exit_code, mode=mode_for_error)
        )
        code = failure.exit_code
        print(str(failure), file=sys.stderr)
    sys.stdout.write(canonical_json_bytes(result).decode("utf-8"))
    return code
