# Round 07 local-verifier comparison audit

Scope: every value that the one-command local verifier reads from a release and
either compares with an independent binary64 recomputation or protects as an
exact structural/integrity value. This audit was completed before allocating a
new release identity.

## Decision rules

The verifier now performs two passes for every semantic document comparison.
Pass 1 checks complete dict key sets, list lengths/order, and every unprofiled
leaf exactly. Pass 2 runs numeric comparison only at a path explicitly present
in the frozen contract. Thus an identity, structure, order, or lineage mutation
fails before any tolerated numeric leaf is evaluated.

All numeric profiles require finite values and conjunctive absolute, relative,
and ULP bounds. There are 88 unique path patterns and no recursive `**` path:

| Profile | Paths | Absolute | Relative | ULP | Decision |
|---|---:|---:|---:|---:|---|
| `tight_recomputed_v1` | 43 | `1e-12` | `1e-12` | `8` | Unchanged from round 06 |
| `derived_feature_snapshot_v1` | 42 | `3e-16` | `3e-14` | `151` | Unchanged round-06 feature-only class |
| `top1000_derived_probability_display_v1` | 3 | `2.2499312661442353e-22` | `3.5383660753807325e-15` | `17` | New display-probability-only class; exact observed maxima |

The contract validator freezes the complete set for every profile, so adding a
path, moving a path between profiles, changing a ceiling, or adding a broad
Top-1000 wildcard fails D01.

## Surface classification table

| Surface | Field paths inspected or recomputed | Classification and rationale | Coverage |
|---|---|---|---|
| Release closure and authority | delivery-manifest entries; final-closure, machine-acceptance, checklist, authority, dependency-lock, D01-D14 and A01-A10 hashes/status | Exact integrity. These bind immutable bytes and execution evidence; no numeric tolerance applies. | `test_missing_and_tampered_final_closure_fail`; formal A07/A07b/A08; local inventory check |
| Protected Phase 0-3 inputs | `artifacts/phase-{0,1,2,2.1,3}` inventory; Phase-1 draws SHA-256; each draw `core_fact_sha256` | Exact source-fact identity. The verifier compares inventory/hash bytes and never rebuilds a protected input. | `protected_root_change` and `early_draw` independent mutations; A09 |
| Data/cutoff identity | model and manifest game, issue, `training_cutoff_issue`, `training_cutoff_position`, `forecast_target_position`, `training_count`, dataset/config IDs, selection/report-only indices | Exact temporal and model identity. Positions/counts are integers, not approximate numerics. | independent `cutoff`, `selection_report_overlap`, and `selection_after_report_labels` mutations |
| Model objective and selection | `model.objective_trace.gradient_at_zero_by_zone.*.*`; `model.selection_metrics.*.joint_log_loss`; `selection_receipt.selection_metrics.*.joint_log_loss` | `tight_recomputed_v1`. These are independently recomputed binary64 optimization/metric leaves. All objective structure, candidate/config identity, selected L2, indices, receipt hash, roles, and statuses remain exact. | 8/9-ULP tight-boundary test; model/selection replay; `coefficient`, `model_id`, and selection mutations |
| Model coefficients | `model.zones.*.coefficients.*`; `historical_parent.zones.*.*`; `research_child.zones.*.*` | `tight_recomputed_v1`. Coefficients are recomputed. Model/release IDs derived from frozen stored coefficients, parent/child lineage, research score/result IDs, and manifests are exact. | replay for serving, historical parent, and research child; `coefficient` and `model_id` mutations |
| Model feature context | `model.zones.*.context.ewma_raw.*.*`, `.normalization.*.{mean,scale}`, `.number_features.*.*`, `.pair_matrix.*.*`, `.pair_values.*`, `.recency_gap_raw.*`, `.rolling_raw.*.*` | `tight_recomputed_v1`. These are recomputed binary64 context values. Context game, source count/issue/position, prefix hash, feature IDs/groups, transforms/methods, integer counts, last numbers, and matrix/list shape/order remain exact in pass 1. | full replay; exact-first generic comparator; rolling/EWMA/gap/pair mutations |
| Model normalization/probability | `model.zones.*.top_zone_rows.*.0`, `.log_normalizer`, `.probability_square_sum`, `.normalization_mass`, `.minimum_score`, `.maximum_score`, `.minimum_probability`, `.maximum_probability` | `tight_recomputed_v1`. These are complete-space recomputations. Combination counts, probability-layer lower bounds, normalization method/representation, score-layer histogram identities/counts, and Top-zone ticket tuples/order are exact. | replay normalization; A02 probability/ranking tests |
| Feature snapshots | `feature_snapshot.*.feature_values.F01` through `F14`; `feature_snapshot.*.normalization.F01.{mean,scale}` through `F14.{mean,scale}` | `derived_feature_snapshot_v1`, exactly 42 enumerated leaf paths. This retains round-06 151-ULP cancellation evidence without broadening any raw-feature class. | 32-vector macOS fixture; 151 pass/152 fail; absolute/relative outside; non-finite rejection |
| Feature raw leaves | `feature_snapshot.*.raw.ewma_rates.{10,30}`, `.raw.recency_gap`, `.raw.rolling_rates.{10,30,60}` | `tight_recomputed_v1`. Only the six named raw binary64 leaves are approximate. | feature snapshot replay and existing strict mutation tests |
| Feature snapshot structure/facts | row count/order/key set; game/zone/type; number(s)/reference combination; feature/group/generator IDs; issues, target/cutoff/source positions; `input_prefix_sha256`; rules/comparator/order; availability; windows/methods; all unprofiled raw leaves | Exact structural, ordering, cutoff, and fact identity, completed before the feature numeric pass. Snapshot file SHA-256 remains exact against its manifest. | `LocalVerifierFeatureSnapshotContractTests`: key, game, feature ID, cutoff, fact hash, reorder, missing/extra row, type/non-finite/outside profile |
| Formal forecast envelope | provider/model/feature/data/config/code/dependency IDs; model/feature/data/probability-proof hashes; target/cutoff; ranking key/algorithm; lock ID/time/status/create-once; Top-1000 hash; prefix hashes | Exact lineage, lock, cutoff, and file identity. `first_probability`, `last_probability`, and `joint_probability_mass` are locked release summaries, not compared to a fresh binary64 leaf; their source Top-1000/proof files are hash-bound and replayed below. | `provider_reference`, `missing_lineage`, `lock`, and local final-closure mutations; finalizer forecast evidence |
| Formal Top-1000 display probability | `top1000.*.joint_probability` | `top1000_derived_probability_display_v1` only. This is a derived `exp(score-normalizer)` display/recomputation leaf and is not ticket or score identity. | exact r09/macOS rank-623 fixture; 17 pass/18 fail; just-outside absolute/relative; non-finite rejection |
| Historical Top-1000 display probability | `historical_top1000.*.joint_probability` | Same narrow display class because the historical list is independently regenerated by the same probability pipeline. | explicit path-routing test; historical full replay |
| Research shadow Top-1000 display probability | `shadow_top1000.*.joint_probability` | Same narrow display class because the shadow list is independently regenerated by the same probability pipeline. | explicit path-routing test; shadow full replay |
| All Top-1000 score values | `{top1000,historical_top1000,shadow_top1000}.*.log_joint_score` | `tight_recomputed_v1`, unchanged. The score value may be recomputed within 8 ULP, but its frozen binary64 `score_identity` must still equal the release identity exactly. | tight boundary; full three-scope replay; approximate-tie mutation |
| All Top-1000 explanations | `{top1000,historical_top1000,shadow_top1000}.*.explanation.feature_contributions.*` | `tight_recomputed_v1` for F01-F14 contributions. Explanation method, probability-primary flag, feature groups, field set, and feature IDs are exact. | full three-scope replay and exact-first whole-row comparison |
| All Top-1000 identity/ordering leaves | row key set/count; `front_numbers`, `back_numbers`, `canonical_ticket_key`, `rank`, `full_space_rank`, `probability_layer`, `probability_representation`, `ranking_algorithm_id`, `score_identity`, every tie group/key/size/bound/midrank, lineage, explanation metadata | Exact before numeric. Both release and independent lists are independently validated for membership uniqueness, score-derived identity, exact-score ordering, tie grouping, and rank bounds, then compared leaf-for-leaf. | tolerated 17-ULP probability plus ticket/order/rank/tie-key/score-identity/lineage mutations with `numeric_comparison` mocked to fail if reached |
| Backtest/report-only metrics | `model.report_only_metrics.*.{model_joint_log_loss,model_multiclass_brier}`; `.ablation_metrics.*.{joint_log_loss,multiclass_brier}` | `tight_recomputed_v1`. Full-ticket ranks, fold roles, target/sample positions, feature-group/method identity, normalization-mass invariant, top-K booleans, selection separation, and scientific status are exact. | direct held-out and ablation recomputation; `fake_ablation`; Phase-4 backtest tests |
| Research permutation summary | `model.report_only_summary.permutation_evidence.*.samples.*.{permuted_joint_probability,permuted_joint_log_loss}` | `tight_recomputed_v1`. Method, feature group, donor/target position, sample size, report-only indices, and no-self-donor identity are exact. Other locked aggregate summary fields are protected by the model SHA-256/manifest and schema rather than independently recomputed by local acceptance. | direct derangement recomputation; `fake_permutation`; model hash/closure checks |
| Historical score | `score.metrics.{joint_log_loss,actual_joint_probability,multiclass_brier}` | `tight_recomputed_v1`. Hit-at map, issue, rank/null rank and all score/forecast/result/model IDs remain exact. | score recomputation; `score_forecast_mismatch` and `result_target_mismatch` |
| Backtest summary file | `backtests/*/*/summary.json` and report-only JSONL not otherwise listed above | Exact locked evidence via schema, release manifest, and final closure. The local verifier does not compare these duplicate/aggregate binary64 displays to a second recomputation; canonical independently recomputed metric leaves live in the model paths listed above. | schema validation; final manifest/closure; A02 release acceptance |
| Research decision/manifests | diff/candidate/decision/child manifests, serving immutability, score/result hashes, `probability_changed`, `top1000_changed`, promotion flags | Exact research lineage/decision and file integrity. Recomputed child coefficients and shadow rows use the profiles above; no decision identity is tolerant. | `research_without_score`, serving-immutability, child-manifest hash and shadow replay checks |
| Local inspection output | forecast first/last display, model/feature IDs, cutoff/target, ticket count, scientific status | Exact readback after all replay and integrity gates. It is presentation only and introduces no comparison profile. | one-command local acceptance |

## Frozen numeric path inventory

The 43 unchanged `tight_recomputed_v1` paths are exactly:

```text
model.objective_trace.gradient_at_zero_by_zone.*.*
model.selection_metrics.*.joint_log_loss
selection_receipt.selection_metrics.*.joint_log_loss
model.zones.*.coefficients.*
model.zones.*.context.ewma_raw.*.*
model.zones.*.context.normalization.*.mean
model.zones.*.context.normalization.*.scale
model.zones.*.context.number_features.*.*
model.zones.*.context.pair_matrix.*.*
model.zones.*.context.pair_values.*
model.zones.*.context.recency_gap_raw.*
model.zones.*.context.rolling_raw.*.*
model.zones.*.top_zone_rows.*.0
model.zones.*.log_normalizer
model.zones.*.probability_square_sum
model.zones.*.normalization_mass
model.zones.*.minimum_score
model.zones.*.maximum_score
model.zones.*.minimum_probability
model.zones.*.maximum_probability
model.report_only_metrics.*.model_joint_log_loss
model.report_only_metrics.*.model_multiclass_brier
model.report_only_metrics.*.ablation_metrics.*.joint_log_loss
model.report_only_metrics.*.ablation_metrics.*.multiclass_brier
model.report_only_summary.permutation_evidence.*.samples.*.permuted_joint_probability
model.report_only_summary.permutation_evidence.*.samples.*.permuted_joint_log_loss
historical_parent.zones.*.*
research_child.zones.*.*
score.metrics.joint_log_loss
score.metrics.actual_joint_probability
score.metrics.multiclass_brier
top1000.*.log_joint_score
top1000.*.explanation.feature_contributions.*
historical_top1000.*.log_joint_score
historical_top1000.*.explanation.feature_contributions.*
shadow_top1000.*.log_joint_score
shadow_top1000.*.explanation.feature_contributions.*
feature_snapshot.*.raw.ewma_rates.10
feature_snapshot.*.raw.ewma_rates.30
feature_snapshot.*.raw.recency_gap
feature_snapshot.*.raw.rolling_rates.10
feature_snapshot.*.raw.rolling_rates.30
feature_snapshot.*.raw.rolling_rates.60
```

The 42 retained feature paths explicitly name F01-F14 once under
`feature_snapshot.*.feature_values.<feature>` and twice under
`feature_snapshot.*.normalization.<feature>.{mean,scale}`. The new class is
exactly these three paths and no others:

```text
top1000.*.joint_probability
historical_top1000.*.joint_probability
shadow_top1000.*.joint_probability
```

Conclusion: no remaining local semantic comparison applies exact equality to an
independently recomputed binary64 leaf, and no integrity, identity, ordering,
cutoff, fact, lock, hash, or closure field is routed through numeric tolerance.

