"""Restore runtime dependencies for the hardened acceptance environment.

The Phase 3 acceptance verifier runs every frozen verification command under
``env -i`` with ``HOME=/nonexistent`` and no inherited variables.  Because
CPython derives the per-user ``site-packages`` directory from ``HOME``, the
project's declared runtime dependencies (``jsonschema``, ``numpy``, ...) --
which the provider installs into the task-home user site -- silently drop off
``sys.path`` and become unimportable.  Without restoration, every module that
transitively imports :mod:`jsonschema` (for example
:mod:`lottery_research.phase3.pit_recovery` or
:mod:`lottery_research.phase2_1.schema`) fails to import, so the PIT tamper
matrix, the independent validator and the phase-2.1 readiness revalidation all
fail before any scientific logic runs.

This module locates that user site by walking upward from a project root until
it finds ``<dir>/home/.local/lib/pythonX.Y/site-packages`` (the layout used by
both the working copy and the acceptance checkout), inserts it onto
``sys.path``, and records the resolved directory in the
:data:`ENV_VAR` environment variable.  Child processes that run from a sandboxed
copy which does not carry this module -- notably the phase-2.1 readiness
revalidation, which executes from a ``/tmp`` tree -- inherit that variable and
re-add the directory themselves via ``scripts/phase2_1/bootstrap.py``.

The helper never adds credentials, never changes behaviour when the directory is
absent (e.g. an ordinary developer environment), and never derives feature
availability.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Environment variable carrying the restored user-site directory.  A parent
#: process that located the user site sets this so sandboxed child processes
#: (which cannot run this module) can restore the same directory.
ENV_VAR = "LOTTERY_USER_SITE"


def _site_packages_subdir() -> Path:
    return (
        Path("home")
        / ".local"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def restore_user_site(project_root: Path) -> Path | None:
    """Insert the task-home user site onto ``sys.path`` if it can be found.

    The search prefers an inherited :data:`ENV_VAR` (set by a parent process)
    so that copies relocated outside the task tree still resolve the original
    user site, then falls back to walking upward from ``project_root``.  The
    resolved directory is exported back through :data:`ENV_VAR` so descendants
    inherit it.  Returns the inserted directory, or ``None`` when no user site
    is present.
    """
    candidates: list[Path] = []
    inherited = os.environ.get(ENV_VAR)
    if inherited:
        candidates.append(Path(inherited))
    marker = _site_packages_subdir()
    for ancestor in project_root.resolve().parents:
        candidates.append(ancestor / marker)
    for candidate in candidates:
        if candidate.is_dir():
            os.environ[ENV_VAR] = str(candidate)
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    return None
