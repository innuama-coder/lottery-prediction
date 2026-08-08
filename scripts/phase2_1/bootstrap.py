from __future__ import annotations

import os
import sys
from pathlib import Path


def _restore_user_site() -> None:
    """Restore the task-home user site in the hardened acceptance env.

    The verifier runs commands under ``env -i HOME=/nonexistent``; a parent
    process exports the user site it restored as ``LOTTERY_USER_SITE`` (see
    ``src/acceptance_env.py``, reached via ``src/sitecustomize.py`` or the
    top-level ``lottery_research`` import bridge).  This runs inside sandboxed
    ``/tmp`` copies (e.g. the readiness revalidation) which do not carry those
    bootstrap modules, so it reads the inherited variable directly instead of
    importing them.  No-op in an ordinary environment.
    """
    user_site = os.environ.get("LOTTERY_USER_SITE")
    if user_site and Path(user_site).is_dir() and user_site not in sys.path:
        sys.path.insert(0, user_site)


def activate() -> Path:
    root = Path(__file__).resolve().parents[2]
    venv_python = root / ".phase2_1/venv/bin/python"
    if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
        os.execv(venv_python, [str(venv_python), *sys.argv])
    source = str(root / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    _restore_user_site()
    return root
