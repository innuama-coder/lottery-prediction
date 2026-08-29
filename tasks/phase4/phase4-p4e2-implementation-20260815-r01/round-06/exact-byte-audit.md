# Round 06 exact-byte and derived-float audit

Scope: the one-command local verifier, its finalizer, and every remaining file
byte/hash comparison reached by local acceptance.

## Corrected semantic recomputation

The sole derived-float byte comparison was
`feature-snapshot.jsonl == canonical(independent_recomputation)`. It is removed.
Snapshot rows now compare structurally in exact row order with exact field sets.
Game, zone, row kind, number/pair/reference identity, feature IDs, feature groups,
source/cutoff/target positions and issue, prefix fact hash, rule/order semantics,
available-at value, windows, methods, and every other non-authorized leaf remain
exact. Only explicitly named numeric leaves route to a frozen profile:

- feature values F01-F14 and normalization F01-F14 mean/scale use
  `derived_feature_snapshot_v1`: finite, conjunctive absolute `3e-16`, relative
  `3e-14`, and at most 151 ULP;
- raw EWMA, rolling-rate, and recency-gap leaves are explicitly named and retain
  `tight_recomputed_v1`: finite, conjunctive absolute/relative `1e-12`, and at
  most 8 ULP;
- model coefficients/objective trace, model contexts, metrics, authorized
  Top-1000 display/contribution leaves, historical replay, and shadow replay keep
  the existing tight profile.

No recursive wildcard exists. Each F01-F14 snapshot value and normalization
statistic is enumerated by feature ID. The 151-ULP ceiling is limited by both
tighter absolute and relative ceilings and is justified by the controller's 32
cross-runtime vectors. Tests reject the first selected test value above each of
the three maxima, including 152 ULP.

## Exact byte/hash comparisons retained

- Release snapshot bytes against `manifest.json.snapshot_sha256`: exact integrity.
- Model, forecast, Top-1000, lifecycle, research-child, score/result, protected
  inventory, dependency lock, authority, command receipt, manifest, acceptance,
  and final-closure hashes: exact identity, lineage, lock, or closure integrity.
- Release inventory before/after local verification: exact read-only guarantee.
- Canonical local-contract document comparison: exact contract identity while
  ignoring presentation whitespace only; it performs no numeric recomputation.
- Finalizer create-once collision comparison: exact immutable-file identity.
- Score/tie binary64 identities and canonical ticket membership/order/rank:
  exact frozen ranking identity, not a local recomputation tolerance.

Conclusion: no remaining exact-byte comparison conflates independent recomputed
derived floats with release integrity. Release bytes are never rewritten or
re-hashed from a tolerated local expectation.
