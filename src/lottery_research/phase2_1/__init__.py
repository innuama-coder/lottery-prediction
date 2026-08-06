"""Versioned Phase 2.1 research and acceptance workflow."""

RUN_LABEL = "P2.1-R00"
BASELINE_SHA = "60d02be4dbe9a1b28c5784bc421437712b80392c"
ITERATION = "i02"
RELEASE_ID = f"{RUN_LABEL}-{BASELINE_SHA[:12]}-{ITERATION}"

__all__ = ["BASELINE_SHA", "ITERATION", "RELEASE_ID", "RUN_LABEL"]
