"""Restore the per-user site-packages on Python startup.

The Phase 3 acceptance verifier intentionally runs the frozen verification
commands inside a hardened, minimal environment (``env -i`` with
``HOME=/nonexistent`` and no inherited variables).  A side effect is that the
interpreter never adds the per-user ``site-packages`` directory to ``sys.path``
(it is derived from ``HOME``), so the project's declared runtime dependency
``jsonschema[format]`` -- which the provider installs into that user site --
becomes unimportable.  Without restoration, every Phase 3 test module that
imports :mod:`lottery_research.phase3.schema` (transitively) fails to import.

``src`` is placed on ``sys.path`` by the verification command
(``PYTHONPATH=src``) before ``site`` imports ``sitecustomize``, so this module
runs automatically.  It only re-adds the task-home user site (located relative
to this file: ``src/ -> acceptance-checkout -> evidence -> <task-dir>``); it
does not add credentials, does not change behaviour when the directory is absent,
and never derives feature availability.
"""

from __future__ import annotations

import sys
from pathlib import Path

_USER_SITE = (
    Path(__file__).resolve().parents[3]
    / "home"
    / ".local"
    / "lib"
    / "python3.12"
    / "site-packages"
)

if _USER_SITE.is_dir() and str(_USER_SITE) not in sys.path:
    sys.path.insert(0, str(_USER_SITE))
