# Path-pattern classification

Contract: `P4-LOCAL-PATH-CLASSIFIED-BINARY64-5`, schema `1.6.0`.

All 86 numeric patterns remain enumerated exactly once. Only the two c5a9
failure classes move; no identity field or unbounded wildcard is added.

| Profile | Patterns | Absolute | Relative | ULP | Change |
|---|---:|---:|---:|---:|---|
| `tight_recomputed_v1` | 35 | `1e-12` | `1e-12` | 8 | unchanged bounds; propagated zone score removed |
| `derived_feature_context_v2` | 44 | `3 * 2^-53` | `3e-14` | 151 | unchanged bounds; nested number features removed |
| `derived_number_feature_context_v1` | 1 | `4 * 2^-53` | `3e-14` | 151 | new isolated nested context class |
| `derived_coefficient_v1` | 2 | `1e-12` | `1e-12` | 16 | unchanged; c5a9 zero failures |
| `propagated_zone_score_v1` | 1 | `4 * 2^-53` | `2^-46` | 64 | new isolated propagated score class |
| `top1000_derived_probability_display_v3` | 3 | `2^-71` | `17 / 2^52` | 32 | unchanged; c5a9 zero failures |

The nested absolute and zone absolute/ULP bounds equal the controller maxima.
The zone relative bound is the smallest binary power strictly above its
independent maximum. Every profile retains finite-required conjunctive
absolute, relative, and ULP semantics. Stable score keys, score/tie identities,
ticket membership/order/rank, lineage, hashes, IDs, and create-once files remain
exact.
