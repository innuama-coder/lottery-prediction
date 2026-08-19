"""Sequence-safe Phase 4E3 feature research; never imported by r12 serving."""

from .model import FEATURE_FAMILIES, build_context, fit_zone, zone_distribution

__all__ = ["FEATURE_FAMILIES", "build_context", "fit_zone", "zone_distribution"]
