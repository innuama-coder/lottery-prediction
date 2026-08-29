# Round 08 score/tie identity migration audit

This audit classifies every active P4E2 score/ranking surface after removal of
raw-binary64 identity semantics. Preserved releases and prior-round evidence
retain their historical bytes and identifiers; they are not active builders or
validators for a new release.

## Stable mathematical order key

- key ID: `P4S10HE1`
- canonical input: the exact rational value of a finite binary64 score
- resolution: decimal quantum `0.0000000001` (`1e-10`)
- rounding: IEEE-style `ROUND_HALF_EVEN`
- canonical serialization: `P4S10HE1:<signed base-10 integer tick>`
- rank comparator: stable tick descending, then canonical ticket ascending
- score identity: `score-order-key-v1:<score_order_key>`
- tie key/group: `tie-score-order-key-v1:<score_order_key>` and
  `tie-group-score-order-key-v1:<score_order_key>`

The quantum is more than 3.5 million times the observed `2.8e-17`
cross-runtime drift. It is 4.326 times smaller than the minimum adjacent
distinct score `4.326295779955025012e-10` across the six preserved r10 scopes.
All 6,000 r10 rows are unchanged under one `nextafter` step in either direction;
all adjacent distinct scores remain distinct; every canonical ticket,
membership, order, and rank is unchanged. The closest r10 score remains
`4.41153785999404e-15` from the first `1e-10` half-quantum boundary. The six
controller pairs produce identical product and independent keys/identities.

## Exact-versus-semantic classification

| Surface | Exact integrity | Semantic numeric replay |
|---|---|---|
| Formal, historical, shadow Top-1000 | row shape/count, ticket membership, canonical key, order/rank, `score_order_key`, `score_identity`, representation/ranking IDs, layer, all tie IDs/sizes/bounds/midrank, lineage and explanation metadata | only enumerated `log_joint_score`, feature contribution, and display `joint_probability` leaves |
| Complete-space zone summaries | stable-key representation/ID/quantum/rounding, combination count and non-single-score lower bound; complete-space per-key identities are deliberately not serialized because the formal identity scope is Top-1000 | normalization score/probability leaves already named by the tight profile |
| Probability qualification | stable semantics/key/ranking IDs, zone order contracts, Top-1000 key counts/digest, one-ULP invariant | dynamic-range display numbers remain locked model evidence; no identity tolerance |
| Formal forecast and locks | representation/ranking/key identifiers, model/feature/data/config/code/dependency lineage, hashes, target/cutoff, prefixes, create-once linkage | none for identity fields; only independently replayed Top-1000 numeric leaves use named profiles |
| Historical lifecycle and forecast | ranking ID, ticket/key/tie fields, forecast/result/score lineage and hashes | named lifecycle metric and Top-1000 numeric leaves only |
| Research child/shadow | independently rebuilt child contexts, coefficients, complete spaces and stable-key Top-1000; child/parent/score/result lineage and serving immutability exact | named model/Top-1000 numeric leaves only |
| Builder/finalizer/local CLI | schemas and constants reject prior representation/ranking IDs; all stable key/tie fields recomputed before acceptance | no wildcard or identity tolerance |
| Independent oracle/replay/mutations | independent rational-to-tick implementation; complete child enumeration; stable key and all derived identities recomputed; key/tie/order mutations fail closed | same frozen path router as local acceptance |
| Model cards/runbook/receipts/manifests | new versioned semantics and release hashes exact | none |

`top1000_derived_probability_display_v1` remains exactly the same three paths
and the same 17-ULP conjunctive bounds. `derived_feature_snapshot_v1` remains
exactly the same 42 paths and 151-ULP conjunctive bounds. The two former broad
container patterns for historical/research zones were removed; those surfaces
now reuse the explicit `model.zones.*...` leaves and cannot bypass independent
leaf validation.

## Fail-closed coverage

- first stable-key boundary changes key, score identity, tie key, and tie group
- same stable key shares score/tie identity; different keys cannot share them
- NaN and both infinities are rejected by product and oracle
- Top-1000 stable key, score identity, tie key/group, probability layer, tie bounds,
  ranking ID, representation ID, ticket, order, rank, and lineage remain exact
- mutation suite independently changes stable key and tie key and retains the
  approximate-tie attack; each must be detected
- `p4e2-ranking.schema.json`, `probability-qualification.schema.json`, the model
  score-order-contract schema, formal forecast schema, frozen local contract, builder,
  finalizer, local replay, and CLI all bind the new identifiers
- preserved-r10 migration replay is read-only and accepts only the exact 178-file
  inventory `e3b65e2ef7c7ab12ee7fe21c68d9847858661446f1a5242528db0dc46ba19d5c`

No active source, schema, builder, finalizer, validator, replay, CLI, or runbook
retains the old binary64 score-identity representation or ranking identifier.
