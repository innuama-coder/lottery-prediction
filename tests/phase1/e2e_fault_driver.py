from __future__ import annotations

import argparse
import threading
from pathlib import Path
from unittest.mock import patch

from lottery_data.steps.preflight import BootstrapArguments
from lottery_data.steps.publication_journal import PublicationJournal
from lottery_data.workflow import execute_bootstrap


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    args = parser.parse_args()
    original = PublicationJournal.advance

    def durable_window(journal: PublicationJournal, state: str, *, updated_at_utc: str) -> None:
        original(journal, state, updated_at_utc=updated_at_utc)
        if state == "POINTER_COMMITTED":
            args.marker.write_text("POINTER_COMMITTED\n", encoding="ascii")
            print("READY POINTER_COMMITTED", flush=True)
            threading.Event().wait()

    with patch.object(PublicationJournal, "advance", new=durable_window):
        execute_bootstrap(BootstrapArguments(
            mode="bootstrap", source_mode="snapshot", phase0_snapshot=args.snapshot,
            artifacts_root=args.artifacts_root, config_root=args.config,
            run_id="e2e07-crash", release_id="e2e07-crash-release",
        ))
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
