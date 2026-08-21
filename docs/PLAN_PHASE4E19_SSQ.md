# Phase4E19 SSQ prize-aware optimization plan

## Objective

Optimize only the 双色球 model. The hard acceptance target is an average realized prize strictly greater than 2 yuan per complete ticket for every registered partition size where the partition is reported, with the primary decision based on the 120-draw truth set and separately confirmed on the frozen 60-draw outer evaluation split.

## Scope and boundaries

- Game scope: SSQ only (6 red + 1 blue).
- DLT model, parameters, artifacts, hashes, and serving release are read-only.
- P4E6 serving remains `P4-P4E2-20260815-r12`, `PROSPECTIVE_ONLY`.
- No outer labels may enter feature construction, candidate selection, or model fitting.
- All candidates are selected before the frozen outer evaluation rows are loaded.
- Fixed prize benchmarks: first prize 5,000,000 yuan; second prize 100,000 yuan; remaining SSQ fixed tiers follow the registered rule table.

## Primary metric and partitions

For each target draw and each `N` in `1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000`, rank complete SSQ tickets and compute:

`partition_average_prize = known_prize_total_yuan / N`

The 120-draw aggregate is total known prize divided by `120 * N`. Unwinning tickets contribute zero. Hit rate and winning-ticket count are diagnostics only.

## Model design

1. Build independent red and blue probability heads with registered windows 360, 720, and 1200 draws.
2. Add SSQ-only features: red-zone distribution, parity, sum band, consecutive-run structure, red/blue parity interaction, blue recency/transition, and historical combination similarity.
3. Convert head outputs into complete-ticket scores using expected prize contribution across all six prize tiers.
4. Apply a deterministic coverage/diversity constraint so the top-N portfolio does not duplicate near-identical red/blue structures.
5. Register a small fixed candidate family before inner selection; no unbounded hyperparameter search.

## Selection and validation

- Inner selection: 240 pre-outer draws, four chronological blocks of 60.
- Candidate eligibility: positive prize uplift versus raw-control baseline in at least three blocks and no block with catastrophic collapse below the random baseline confidence floor.
- Candidate ranking: median block uplift, then lower-tail block uplift, then registered order.
- Outer validation: frozen 120 draws split into calibration 60 and evaluation 60.
- Promotion gate: calibration average >2, evaluation average >2, all-120 average >2 for the primary N set; no promotion if any gate fails.

## Deliverables

- SSQ-only implementation under `scripts/phase4e19/`.
- Machine-readable report with candidate registry, feature lineage, strict-lag hashes, partition totals, averages, prize tiers, baseline comparisons, gate results, and DLT isolation hashes.
- Focused tests for rule scoring, ticket ranking, partition arithmetic, no-label leakage, and DLT immutability.
- Delivery document and commit/push on the existing feature branch.
