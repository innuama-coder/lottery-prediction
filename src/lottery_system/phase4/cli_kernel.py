from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .serialization import canonical_json_bytes, load_json, sha256_file
from .storage import IdentityReuseError, LockUnavailable, SecurityBoundaryError, resolve_inside, safe_relative_path


Provider = Callable[[argparse.Namespace], Mapping[str, Any]]
_GIT_EXECUTABLE = shutil.which("git") or "git"


class HoldError(RuntimeError):
    exit_code = 20


class RetryableTerminal(RuntimeError):
    exit_code = 30


class ContractEvidenceMismatch(ValueError):
    exit_code = 5


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[tuple[str, str], Provider] = {}

    def register(self, verb: str, action: str, provider: Provider) -> None:
        key = (verb, action)
        if key in self._providers:
            raise ValueError(f"duplicate Phase 4 CLI provider: {' '.join(key)}")
        self._providers[key] = provider

    def provider(self, verb: str, action: str) -> Provider | None:
        return self._providers.get((verb, action))

    @property
    def registered(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._providers)


PROVIDER_MODULES = (
    "contract", "data_core", "data_official", "calendar", "probability_validation",
    "forecast", "result_unlock", "score", "research", "schedule", "state",
    "replay", "validation", "release",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def parse_clock(value: str) -> str:
    if value == "system":
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not value.startswith("fixture:"):
        raise ContractEvidenceMismatch("clock must be system or fixture:<RFC3339>")
    raw = value.split(":", 1)[1]
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractEvidenceMismatch("fixture clock is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise ContractEvidenceMismatch("fixture clock must include an offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def source_commit(root: Path) -> str:
    try:
        value = subprocess.run(
            [_GIT_EXECUTABLE, "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractEvidenceMismatch("source commit cannot be resolved") from exc
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ContractEvidenceMismatch("source commit is invalid")
    return value


EXECUTION_CONTEXT_VARIABLES = (
    "P4_ACTOR_ID",
    "P4_SESSION_ID",
    "P4_TASK_ID",
    "P4_ROLE",
    "P4_ACTOR_ASSIGNMENTS",
)


def producer_provenance(root: Path, path: str) -> dict[str, str]:
    context = {name: os.environ.get(name, "").strip() for name in EXECUTION_CONTEXT_VARIABLES}
    present = {name for name, value in context.items() if value}
    if present != set(EXECUTION_CONTEXT_VARIABLES):
        state = "missing" if not present else "partial"
        raise ContractEvidenceMismatch(f"Phase 4 invocation provenance context is {state}")
    assignment_relative = safe_relative_path(context["P4_ACTOR_ASSIGNMENTS"])
    if (
        not assignment_relative.startswith("artifacts/phase-4-prep/")
        or not assignment_relative.endswith("/control/actor-assignments-preparation.json")
    ):
        raise ContractEvidenceMismatch("actor assignment is outside the installed preparation control root")
    assignment_path = resolve_inside(root, assignment_relative)
    assignment = load_json(assignment_path, reject_floats=True)
    matches = [row for row in assignment.get("assignments", []) if row.get("actor_id") == context["P4_ACTOR_ID"]]
    if len(matches) != 1:
        raise ContractEvidenceMismatch("invocation actor is not uniquely assigned")
    actor = matches[0]
    if (
        actor.get("actor_type") != "codex_session"
        or actor.get("session_id") != context["P4_SESSION_ID"]
        or context["P4_TASK_ID"] not in actor.get("task_ids", [])
        or context["P4_ROLE"] not in actor.get("roles", [])
    ):
        raise ContractEvidenceMismatch("invocation provenance does not match the actor assignment")
    record_path = resolve_inside(root, actor["task_record_path"])
    if not record_path.is_file() or sha256_file(record_path) != actor["task_record_sha256"]:
        raise ContractEvidenceMismatch("invocation actor task record hash mismatch")
    record = load_json(record_path, reject_floats=True)
    for key in ("actor_id", "actor_type", "session_id", "roles", "task_ids"):
        if record.get(key) != actor.get(key):
            raise ContractEvidenceMismatch("invocation actor task record identity mismatch")
    safe_relative_path(path)
    return {
        "producer_actor_id": context["P4_ACTOR_ID"],
        "task_id": context["P4_TASK_ID"],
        "session_id": context["P4_SESSION_ID"],
        "source_commit": source_commit(root),
        "path": path,
        "role": context["P4_ROLE"],
    }


def _load_providers(registry: ProviderRegistry) -> None:
    for name in PROVIDER_MODULES:
        try:
            module = importlib.import_module(f"lottery_system.phase4.commands.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"lottery_system.phase4.commands.{name}":
                continue
            raise
        register = getattr(module, "register", None)
        if register is not None:
            register(registry)


def _add_flag(parser: argparse.ArgumentParser, flag: str, *, required: bool = True) -> None:
    destination = flag[2:].replace("-", "_")
    if any(action.dest == destination for action in parser._actions):
        return
    path_flags = {
        "--runtime-root", "--release-root", "--config", "--schemas", "--authority-receipt",
        "--output", "--actor-assignments", "--source-policy", "--staging-root", "--calendar-policy", "--genesis",
        "--calendar", "--schedule", "--oracle", "--registry", "--manifest", "--replay",
        "--validator", "--review", "--delivery-statement", "--correction-policy", "--preregistration",
    }
    parser.add_argument(flag, required=required, type=Path if flag in path_flags else str)


def build_parser(root: Path | None = None) -> tuple[argparse.ArgumentParser, dict[tuple[str, str], dict[str, Any]]]:
    root = (root or project_root()).resolve()
    contract = load_json(root / "config/phase4/cli-contract.json")
    parser = argparse.ArgumentParser(prog="python -m lottery_system.phase4")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verb_parsers: dict[str, argparse.ArgumentParser] = {}
    specifications: dict[tuple[str, str], dict[str, Any]] = {}
    for row in contract["commands"]:
        verb, action = row["verb"].split(" ", 1)
        if verb not in verb_parsers:
            parent = verbs.add_parser(verb)
            parent.set_defaults(_verb_parser=parent)
            verb_parsers[verb] = parent
            parent._phase4_actions = parent.add_subparsers(dest="action", required=True)  # type: ignore[attr-defined]
        action_parser = verb_parsers[verb]._phase4_actions.add_parser(action)  # type: ignore[attr-defined]
        for raw in row["required_flags"]:
            alternatives = raw.split("|")
            if len(alternatives) == 1:
                _add_flag(action_parser, alternatives[0])
            else:
                group = action_parser.add_mutually_exclusive_group(required=True)
                for flag in alternatives:
                    group.add_argument(
                        flag,
                        dest=flag[2:].replace("-", "_"),
                        type=Path if flag.endswith("-root") else str,
                    )
        specifications[(verb, action)] = row
    data_release = next(
        item for item in specifications if item == ("data", "release")
    )
    del data_release
    action_parser = next(
        action for action in verb_parsers["data"]._phase4_actions.choices.values() if action.prog.endswith(" data release")  # type: ignore[attr-defined]
    )
    action_parser.add_argument("--result-revision-id", action="append", default=[])
    return parser, specifications


def _error_payload(command: str, code: int, terminal: str, error: str) -> dict[str, Any]:
    return {"command": command, "status": "HOLD" if code in {20, 30} else "FAIL", "terminal": terminal, "exit_code": code, "error": error}


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    try:
        parser, specifications = build_parser()
        args = parser.parse_args(argv)
        command = f"{args.verb} {args.action}"
        registry = ProviderRegistry()
        _load_providers(registry)
        provider = registry.provider(args.verb, args.action)
        if provider is None:
            result: Mapping[str, Any] = {
                "command": command,
                "status": "HOLD",
                "terminal": "HOLD_COMMAND_NOT_IMPLEMENTED",
                "exit_code": 20,
            }
            code = 20
        else:
            result = dict(provider(args))
            status = result.get("status")
            code = int(result.get("exit_code", 0 if status in {"PASS", "READY"} else 20))
            if code not in specifications[(args.verb, args.action)]["allowed_exit_codes"]:
                raise ContractEvidenceMismatch("provider emitted an unregistered exit code")
            result = {"command": command, **result, "exit_code": code}
    except IdentityReuseError as exc:
        code, result = 4, _error_payload(command, 4, "IDENTITY_REUSE", str(exc))
    except SecurityBoundaryError as exc:
        code, result = 6, _error_payload(command, 6, "SECURITY_OR_CAUSALITY_FAILURE", str(exc))
    except LockUnavailable as exc:
        terminal = getattr(exc, "terminal", "RETRYABLE_TERMINAL_RECORDED")
        code, result = 30, _error_payload(command, 30, terminal, str(exc))
    except RetryableTerminal as exc:
        code, result = 30, _error_payload(command, 30, "RETRYABLE_TERMINAL_RECORDED", str(exc))
    except HoldError as exc:
        code, result = 20, _error_payload(command, 20, "HOLD", str(exc))
    except (ContractEvidenceMismatch, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        code, result = 5, _error_payload(command, 5, "CONTRACT_OR_EVIDENCE_MISMATCH", str(exc))
    encoded = canonical_json_bytes(dict(result))
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(encoded.decode("utf-8"))
    else:
        stream.write(encoded)
    return code
