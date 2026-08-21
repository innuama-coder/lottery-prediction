#!/usr/bin/env python3
"""Run Phase4 unittest discovery with optional module inclusion/exclusion."""

from __future__ import annotations

import argparse
import sys
import unittest
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def flatten(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-module")
    parser.add_argument("--exclude-module")
    arguments = parser.parse_args()
    discovered = unittest.defaultTestLoader.discover("tests/phase4", pattern="test_*.py")
    selected = []
    for test in flatten(discovered):
        module = test.id().split(".", 1)[0]
        if arguments.include_module and module != arguments.include_module:
            continue
        if arguments.exclude_module and module == arguments.exclude_module:
            continue
        selected.append(test)
    print(f"SELECTED_PHASE4_TESTS={len(selected)}", flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
