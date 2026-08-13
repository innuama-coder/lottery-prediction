from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cli_kernel import ProviderRegistry, project_root
from ..release_ops import verify_manifest
from ..provider_registry import register_delivered_provider


def replay_release(args: Any) -> dict[str, Any]:
    release = Path(args.release_root).resolve()
    manifest = verify_manifest(release, Path(args.manifest).resolve())
    return {"status":"PASS","terminal":"REPLAY_INPUT_CLOSED","exit_code":0,"release_id":release.name,"verified_file_count":manifest["file_count"]}


def register(registry: ProviderRegistry) -> None:
    register_delivered_provider(registry, "replay", "release", replay_release)
