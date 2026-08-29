# Numeric path classification audit

Contract: `P4-LOCAL-PATH-CLASSIFIED-BINARY64-4`, schema `1.5.0`.

All 86 previously enumerated numeric patterns remain enumerated exactly once;
no wildcard depth or numeric identity routing was added.

| Profile | Patterns | Absolute | Relative | ULP | Source |
|---|---:|---:|---:|---:|---|
| `tight_recomputed_v1` | 36 | `1e-12` | `1e-12` | 8 | remaining recomputed metrics, scores, normalizers, raw/intermediate context leaves |
| `derived_feature_context_v2` | 45 | `3 * 2^-53` | `3e-14` | 151 | 42 feature snapshot patterns plus model-context `number_features` and normalization mean/scale |
| `derived_coefficient_v1` | 2 | `1e-12` | `1e-12` | 16 | fitted coefficients and objective gradients only |
| `top1000_derived_probability_display_v3` | 3 | `2^-71` | `17 / 2^52` | 32 | formal, historical, and shadow display `joint_probability` only |

The feature absolute ceiling is the exact binary expression of the full
controller maximum. The probability absolute and ULP ceilings are the smallest
binary power/power-of-two envelopes above the full maxima; the relative axis is
the already formula-derived ceiling that remains above the full relative
maximum. The 16-ULP coefficient value is a candidate envelope above the
reported 15-ULP example, not a claimed maximum until the controller preflight
audits every coefficient/objective-gradient comparison.

Normal replay still fails immediately on any bound breach. The preflight uses a
scoped collector that suppresses only that numeric fail-fast long enough to
report the complete matrix. Exact structure and identity comparison runs first
and remains unsuppressed.
