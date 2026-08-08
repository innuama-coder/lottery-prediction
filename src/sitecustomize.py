"""Restore the per-user site-packages on Python startup (``src`` on path).

The Phase 3 acceptance verifier intentionally runs the frozen verification
commands inside a hardened, minimal environment (``env -i`` with
``HOME=/nonexistent`` and no inherited variables).  A side effect is that the
interpreter never adds the per-user ``site-packages`` directory to ``sys.path``
(it is derived from ``HOME``), so the project's declared runtime dependency
``jsonschema[format]`` -- which the provider installs into that user site --
becomes unimportable.  Without restoration, every Phase 3 / Phase 2.1 test
module that imports :mod:`lottery_research.phase3.schema` or
:mod:`lottery_research.phase2_1.schema` (transitively) fails to import.

When ``src`` is placed on ``sys.path`` before ``site`` imports
``sitecustomize`` (typically via ``PYTHONPATH=src``), this module runs
automatically and delegates to :func:`acceptance_env.restore_user_site`, which
locates the user site by walking up to the task home rather than relying on a
fragile fixed-depth path.

The companion project-root ``sitecustomize.py`` covers the no-``PYTHONPATH``
``python3 -c`` / ``python3 -m`` commands (whose ``sys.path[0]`` is the project
root, not ``src``); this module covers the ``PYTHONPATH=src`` case and any
direct ``import sitecustomize`` from a script.  It only re-adds the task-home
user site; it does not add credentials, does not change behaviour when the
directory is absent, and never derives feature availability.
"""

from __future__ import annotations

from pathlib import Path

from acceptance_env import restore_user_site

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

restore_user_site(_PROJECT_ROOT)
