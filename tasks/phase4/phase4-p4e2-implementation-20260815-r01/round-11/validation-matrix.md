# Linux validation matrix

Status: `PASS`

| Gate | Result |
|---|---|
| authority and contract pre-build | PASS |
| create-once dual-game build | PASS |
| independent replay | 100% match; 28/28 mutations detected; product imports 0 |
| A01 compileall | PASS |
| A02 Phase 4 | 166 PASS, 20 audited superseded-only skips, 3218.457 s |
| A03 independent Oracle | 18 PASS, 0.141 s |
| A04 Phase 3 | 69 PASS, 179.503 s |
| A05 Phase 2.1 frozen suite | 39 PASS, 114.348 s |
| A06 Phase 2 frozen suite | 31 PASS, 3.345 s |
| A07/A07b authority checkers | PASS / PASS |
| A08 contract/schema | PASS; 12 schemas, 31 negatives |
| A09 independent check-only replay | PASS |
| A10 product CLI replay validation | PASS |
| finalizer and bottom-up product validate | PASS |
| Linux public local acceptance | PASS; release unchanged |
| build/finalizer reuse negatives | rejected / rejected |
| r11 and Phase 0-3 protected diffs | zero / zero |

Replay facts:

- SSQ/DLT semantic comparisons: 54,807 / 54,865;
- formal rows: 1,000 per game;
- exact stable score/tie identities, ticket membership/order/rank, lineage,
  hashes, and locks retained;
- mutation detection: 28/28, including draw/cutoff/features/coefficient,
  probability, ordering, stable score key, tie key, lineage, lock, protected
  root, provider, schedule, lifecycle, and shallow validation attacks;
- numeric contract: `P4-LOCAL-PATH-CLASSIFIED-BINARY64-5`.
