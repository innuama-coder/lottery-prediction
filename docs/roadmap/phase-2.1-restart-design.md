# Phase 2.1 Restart and VPS Execution Design

Status: approved design; VPS preparation plan available

## 1. Purpose

Phase 2 remains `HOLD / partially achieved`. Its historical audit and
`indeterminate` scientific classification are retained as historical evidence,
but its former `GO` delivery artifact is not authority for a new execution.
Phase 2.1 is a versioned corrective release that repairs the known methodological
and acceptance defects before any new formal statistical result is generated.

This document defines the controlled remote-VPS execution model and the
preparation gate required before Phase 2.1 can enter its formal G0/G1 workflow.
It deliberately does not authorize model research, prediction, number ranking,
or betting work.

## 2. Version and Evidence Boundary

The following Phase 2 materials are read-only historical inputs:

- `docs/roadmap/phase-2-randomness-audit-plan.md` and
  `docs/roadmap/phase-2-acceptance-contract.json`;
- `artifacts/phase-2/contracts/preregistration.json`;
- all existing `artifacts/phase-2/results/`, `reviews/`, `replay/`, `runs/`,
  and `acceptance/` artifacts.

Phase 2.1 must not overwrite a historical preregistration, alter an old SHA-256
claim, or write a formal artifact under `artifacts/phase-2/`. Every Phase 2.1
run uses one immutable release ID of the form `phase2.1-YYYYMMDD-<nonce>`.
All Phase 2.1 evidence is rooted at `artifacts/phase-2.1/<release-id>/` after
verified collection from the VPS.

## 3. VPS Execution Model

The VPS is a controlled execution node, not a data authority or a review role.
It may access the public network only during preparation to fetch the explicitly
selected Git commit, Git LFS objects, and pinned Python dependencies. Formal
qualification, audit, power, replay, and acceptance commands must consume only
the frozen workspace inputs and the prepared dependency set. They must never
retrieve live lottery data, select a latest artifact, or expand the release
scope through the network.

The VPS baseline is:

- Linux on x86_64;
- at least 4 vCPU;
- at least 16 GiB RAM;
- at least 60 GiB free disk before the release workspace is created;
- a supported CPython 3.12 interpreter, whose exact patch version is recorded
  in the Phase 2.1 environment lock.

The baseline rejects obviously inadequate hosts. It does not set the formal
Monte Carlo budget: that budget is determined only from the VPS benchmark
recorded for the exact release environment.

## 4. Release Workspace and Network Policy

The detailed plan will parameterize the VPS root as `P2_VPS_ROOT`, with
`/srv/lottery-prediction` as its documented default. One release has the
following non-overlapping directories:

```text
$P2_VPS_ROOT/releases/<release-id>/repo/       verified Git checkout
$P2_VPS_ROOT/releases/<release-id>/.venv/      isolated Python environment
$P2_VPS_ROOT/releases/<release-id>/wheelhouse/ pinned dependency archive
$P2_VPS_ROOT/releases/<release-id>/work/       temporary execution state
$P2_VPS_ROOT/releases/<release-id>/evidence/   write-once collected evidence
```

Preparation networking is logged with the resolved Git commit, Git LFS object
identities, dependency lock hash, wheelhouse manifest, and installation log.
Once the release identity is frozen, formal commands run from the checked-out
tree and local wheelhouse only. Any missing input, network-dependent command,
or attempt to write outside the release root is an environment failure or
`HOLD`; it is never repaired by fetching a newer input mid-run.

No passwords, SSH private keys, API tokens, or raw shell histories are evidence
artifacts. Transfer credentials are supplied by the VPS operator outside the
repository and are redacted from logs.

## 5. Release Identity and Artifact Flow

Phase 2.1 release identity binds the following before formal statistical work:

1. release ID and selected Git commit;
2. Phase 1 baseline input and schema hashes;
3. Phase 2.1 contract, supplementary preregistration, reviewer assignments,
   and method-review identities;
4. exact interpreter, operating-system, package, wheelhouse, and hardware
   identities;
5. executable code and Schema bundle hashes;
6. fixed run configuration, seed registries, resource budget, and artifact
   root.

The VPS first creates only synthetic benchmark and readiness evidence. After
the new G0/G1 freeze, each formal run writes a unique run manifest and immutable
output directory. At collection time, the executor produces a sorted SHA-256
inventory and compressed evidence bundle. The receiving repository checkout
must recompute the inventory before a bundle is admitted under
`artifacts/phase-2.1/<release-id>/`. A transfer mismatch is `EVIDENCE_MISMATCH`
and invalidates the affected run; it must not be patched in place.

## 6. P2.1-R00 VPS Readiness Gate

`P2.1-R00` is a preparation gate before Phase 2.1 G0. It authorizes only
implementation work and Phase 2.1 entry freezing. It cannot authorize a formal
historical audit, power simulation, replay, final acceptance, or any new
scientific claim.

The gate passes only when all of the following evidence is present and hashes
verify:

| Check | Required evidence | Failure outcome |
| --- | --- | --- |
| Host baseline | Linux/x86_64, vCPU, RAM, free disk, UTC clock and filesystem report | `ENVIRONMENT_FAILURE` |
| Source freeze | Exact Git commit, clean checkout, Git LFS verification, Phase 1 input hashes | `EVIDENCE_MISMATCH` |
| Isolated environment | Exact CPython version, lock hash, wheelhouse inventory, installation transcript | `ENVIRONMENT_FAILURE` |
| Scope isolation | Release-local temp, checkpoint and evidence roots; old Phase 2 paths read-only | `HOLD` |
| Network policy | Preparation log plus formal-command offline/no-live-input check | `HOLD` |
| Synthetic benchmark | Fixed synthetic workload, wall time, peak memory, disk output and hardware identity | `HOLD` |
| Budget derivation | Benchmark-based wall-clock, CPU, memory and storage forecast for the registered grid | `HOLD` |
| Collection rehearsal | Dry-run bundle, source and receiver SHA-256 inventories, zero mismatch | `EVIDENCE_MISMATCH` |
| Role boundary | Executor identity, statistical owner, method reviewer, replay reviewer and final accepter recorded without prohibited overlap | `HOLD` |

`P2.1-R00` records `READY` only after every row passes. Any unsuccessful run
preserves its log and partial evidence under a distinct attempt ID. It cannot
replace an earlier attempt or reuse its run ID.

## 7. Required Phase 2.1 Repair Scope

The remote release must not be frozen until the Phase 2.1 design and
supplementary preregistration specify the following corrections:

1. add a genuinely gradual temporal-drift alternative and retain the old
   two-half step alternative under an accurate name;
2. introduce strict Schemas for qualification, historical-audit, and
   power-envelope results;
3. make final acceptance recompute quantitative metrics from lower-level
   evidence, recursively verify identities, and derive scientific classification
   from audit plus power results;
4. verify actual generator output against independently enumerated exact small
   distributions;
5. replace `effect_interval_95` with terminology that states it is a frozen-grid
   compatibility set unless an actual confidence-interval construction is used;
6. register and analyze enough components, directions, and zones to support the
   claimed bias-family power conclusion;
7. bind contracts, code, environment, inputs, and every downstream artifact to
   the same immutable release identity.

Any change to the scientific meaning of a test, effect threshold, grid, or
Monte Carlo decision rule requires a new Phase 2.1 preregistration identity and
a new independent method review before formal results are run.

## 8. Gate Sequence

```text
Phase 2 historical baseline (read-only)
  -> Phase 2.1 corrective design and supplementary preregistration
  -> VPS bootstrap and P2.1-R00 readiness
  -> implementation and known-answer qualification repairs
  -> Phase 2.1 G0/G1 entry freeze
  -> G2 qualification
  -> G3 audit and G4 power on the same release identity
  -> G5 independent replay
  -> G6 final acceptance and evidence collection verification
```

G3 and G4 may run in parallel only after G2 and only when they read the same
frozen release identity. G5 must use an independently registered reviewer and
seed set. G6 cannot use a VPS executor signature as a substitute for final
acceptance review.

## 9. Operational Failure Rules

- A host below the resource baseline, an unrebuildable environment, missing
  frozen input, or dependency mismatch produces `ENVIRONMENT_FAILURE`.
- A source, artifact, or transfer hash mismatch produces `EVIDENCE_MISMATCH`.
- An unavailable review role, insufficient benchmark-derived budget, prohibited
  network/input use, or incomplete correction produces `HOLD`.
- Failed attempts remain evidence. Operators create a new attempt ID rather
  than deleting, overwriting, or manually normalizing a failed output.

Neither a passing local test suite nor a historical Phase 2 `GO` artifact can
override these outcomes.

## 10. Verification Expectations

Before a Phase 2.1 formal result is accepted, the implementation must provide
automated checks for resource preflight, release-directory isolation, frozen
identity verification, dependency/wheelhouse inventory, no-live-input policy,
benchmark budget derivation, and two-sided evidence-bundle hash verification.
The detailed preparation plan assigns each check an owner, exact command,
expected terminal state, and retained artifact.
