from __future__ import annotations

import os
import sys
from pathlib import Path


def activate() -> Path:
    root = Path(__file__).resolve().parents[2]
    venv_python = root / ".phase2_1/venv/bin/python"
    if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
        os.execv(venv_python, [str(venv_python), *sys.argv])
    source = str(root / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    return root
