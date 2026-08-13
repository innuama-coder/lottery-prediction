from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cli_kernel import ProviderRegistry
from .serialization import load_json


def _unimplemented_provider(command: str):
    def provider(_args: Any) -> Mapping[str, Any]:
        return {
            "status": "HOLD",
            "terminal": "HOLD_COMMAND_NOT_IMPLEMENTED",
            "exit_code": 20,
            "registered_command": command,
        }

    setattr(provider, "phase4_explicit_hold", True)
    return provider


def complete_registry(registry: ProviderRegistry, root: Path) -> frozenset[tuple[str, str]]:
    contract = load_json(root / "config/phase4/cli-contract.json", reject_floats=True)
    parser_commands = {
        tuple(row["verb"].split(" ", 1))
        for row in contract["commands"]
    }
    unexpected = registry.registered - parser_commands
    if unexpected:
        raise ValueError(f"registered providers absent from the CLI contract: {sorted(unexpected)}")
    for verb, action in sorted(parser_commands - registry.registered):
        registry.register(verb, action, _unimplemented_provider(f"{verb} {action}"))
    if registry.registered != parser_commands:
        raise ValueError("CLI provider registry and parser contract are not bidirectionally equal")
    return frozenset(parser_commands)


def explicit_hold_commands(registry: ProviderRegistry) -> frozenset[tuple[str, str]]:
    return frozenset(
        key for key in registry.registered
        if getattr(registry.provider(*key), "phase4_explicit_hold", False)
    )


def register_delivered_provider(registry: ProviderRegistry, verb: str, action: str, provider: Any) -> None:
    """Replace only the explicit placeholder installed by ``complete_registry``.

    Provider modules loaded after the state-composition module may close an
    already registered HOLD.  No delivered provider can replace another
    delivered provider through this path.
    """

    key = (verb, action)
    existing = registry.provider(*key)
    if existing is None:
        registry.register(verb, action, provider)
        return
    if not getattr(existing, "phase4_explicit_hold", False):
        raise ValueError(f"cannot replace delivered Phase 4 CLI provider: {' '.join(key)}")
    registry._providers[key] = provider
