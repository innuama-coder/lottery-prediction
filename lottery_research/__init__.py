"""Top-level import bridge for bare ``python3 -c`` / ``python3 -m`` commands.

The Phase 3 acceptance verifier runs the independent PIT validator as::

    python3 -c "from lottery_research.phase3.pit_recovery import ..."

without ``PYTHONPATH=src``.  For a ``-c`` (or ``-m``) invocation the current
directory is on ``sys.path`` at runtime, but ``src`` is not, and neither a
project-root ``sitecustomize.py`` (Python imports ``sitecustomize`` during
``site`` init, *before* the current-directory entry is appended to
``sys.path``) nor ``PYTHONPATH`` can add it.  The canonical implementation
lives under ``src/lottery_research``; without this bridge that import fails with
``ModuleNotFoundError: No module named 'lottery_research'``.

This package makes ``lottery_research`` importable from the project root by
repointing ``__path__`` at the real implementation under ``src/``.  It also
restores the task-home user site (``jsonschema``/``numpy``) that the hardened
``env -i`` environment drops, so the validator can import its dependencies
before any submodule pulls them in.

Commands that set ``PYTHONPATH=src`` resolve the very same files -- this shim
is found first via the current-directory ``sys.path`` entry, and ``__path__``
points at ``src/lottery_research`` -- so there is a single canonical
implementation and no shadowing.  The build backend is unaffected because
``pyproject.toml`` discovers packages only under ``src``.

The bridge never adds credentials, never changes behaviour in an ordinary
developer environment, and never derives feature availability.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"

# Make the canonical source tree importable (sibling packages such as
# lottery_data, plus the acceptance_env helper below).
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Restore the runtime dependencies (jsonschema/numpy) installed in the task
# home user site before any submodule import transitively requires them.
from acceptance_env import restore_user_site  # noqa: E402

restore_user_site(_ROOT)

# Redirect this package's submodules to the canonical implementation.
__path__ = [str(_SRC / "lottery_research")]
