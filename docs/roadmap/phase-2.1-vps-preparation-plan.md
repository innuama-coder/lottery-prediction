# Phase 2.1 VPS Preparation Implementation Plan

> **Historical plan (completed):** This plan governed the initial VPS
> preparation gate. Phase 2.1 subsequently completed its formal statistical and
> acceptance workflow in release `P2.1-R00-61a99a2c3732-i07-r02`, which finished
> `PASS / GO` with all G0-G6 gates passing. Current authority is
> `docs/research/phase-2.1-overall-design.md` plus the release acceptance JSON;
> the preparation-only stop condition below is not the current project status
> and does not imply any Phase 2.2 work. Generic machine resource thresholds in
> earlier planning material are not acceptance requirements.

> **Historical execution note:** The original plan instructed agentic workers to
> use a task-by-task execution skill. The plan has already been completed; its
> unchecked boxes are preserved as planning history and are not an instruction
> to restart Phase 2.1.

**Goal:** Prepare a versioned Phase 2.1 release on a remote VPS and produce a machine-verifiable `P2.1-R00 READY` artifact without generating formal historical statistical results.

**Architecture:** A new `phase2_1` preparation layer creates an immutable release workspace, verifies its source and Phase 1 inputs, builds a local wheelhouse while preparation networking is allowed, measures the exact VPS environment, and validates a collected evidence bundle. The readiness validator aggregates only these evidence identities and refuses any release that can write to legacy Phase 2 paths or that lacks a budget derived from the VPS benchmark.

**Tech Stack:** CPython 3.12, standard-library Python, JSON Schema Draft 2020-12, `jsonschema`, Git, Git LFS, pip, Bash, SHA-256, `unittest`.

---

## Scope and Stop Condition

This historical plan implemented and documented VPS preparation only. At that
time, the following work was explicitly out of scope until a separately
approved Phase 2.1 scientific implementation plan existed:

- modifying historical Phase 2 preregistration, results, reviews, or acceptance artifacts;
- executing a formal historical audit, power grid, replay, or final acceptance;
- freezing Phase 2.1 scientific thresholds, seeds, grids, or a supplementary preregistration;
- Phase 3 prediction, ranking, or betting work.

The plan's historical stop condition was `P2.1-R00 READY` with no formal Phase
2.1 historical result. That stop condition was later superseded by the completed
formal release and final acceptance.

## Planned File Structure

| File | Responsibility |
| --- | --- |
| `docs/roadmap/phase-2.1-restart-design.md` | Approved architecture and invariant boundaries. |
| `docs/roadmap/phase-2.1-vps-preparation-plan.md` | This execution plan. |
| `docs/roadmap/phase-2.1-acceptance-contract.json` | Machine contract for the preparation gate and its exit codes. |
| `schemas/phase2_1/vps-preflight.schema.json` | Host, source, environment and isolation record validation. |
| `schemas/phase2_1/vps-benchmark.schema.json` | Synthetic benchmark and resource-budget record validation. |
| `schemas/phase2_1/evidence-bundle.schema.json` | Release inventory and two-sided transfer verification. |
| `schemas/phase2_1/readiness.schema.json` | `P2.1-R00` aggregate terminal artifact validation. |
| `scripts/phase2_1/preflight_vps.py` | Read-only VPS preflight collector and validator. |
| `scripts/phase2_1/build_wheelhouse.py` | Build and hash a release-local dependency archive. |
| `scripts/phase2_1/bootstrap_release.py` | Create and validate the release-local workspace without touching legacy evidence. |
| `scripts/phase2_1/benchmark_vps.py` | Run the fixed synthetic workload and derive the bounded resource forecast. |
| `scripts/phase2_1/collect_evidence.py` | Build and verify transfer inventories and evidence bundles. |
| `scripts/phase2_1/validate_phase2_1_readiness.py` | Recompute all `P2.1-R00` checks and emit the terminal artifact. |
| `tests/phase2_1/` | Unit and CLI-contract coverage for every preparation component. |
| `docs/runbooks/phase-2.1-vps-operator.md` | Human operator commands, expected outputs and no-secret policy. |
| `docs/handoff/phase-2-current-status.md` | Link the approved Phase 2.1 design and plan without changing historical status. |

The implementation must use the new `phase2_1` namespaces. It must not modify `scripts/validate_phase2_readiness.py`, `artifacts/phase-2/`, or the legacy Phase 2 schemas to make a new run appear ready.

### Task 1: Define the Phase 2.1 Preparation Contract

**Files:**
- Create: `docs/roadmap/phase-2.1-acceptance-contract.json`
- Create: `schemas/phase2_1/readiness.schema.json`
- Test: `tests/phase2_1/test_contract_schema.py`

- [ ] **Step 1: Write schema tests for the preparation-only boundary**

Create tests that reject a readiness document without a release ID, with a legacy `artifacts/phase-2/` artifact root, or with a formal historical result count above zero.

```python
def test_readiness_rejects_legacy_root_and_formal_results(self) -> None:
    payload = valid_readiness()
    payload["artifact_root"] = "artifacts/phase-2/results"
    payload["formal_historical_result_count"] = 1
    errors = list(validator.iter_errors(payload))
    self.assertTrue(errors)
```

- [ ] **Step 2: Run the focused test and confirm it fails before schemas exist**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_contract_schema -v
```

Expected: failure because the Phase 2.1 schemas and fixtures do not yet exist.

- [ ] **Step 3: Define the contract and schemas**

The contract must define these constants exactly:

```json
{
  "contract_version": "2.1.0-draft",
  "preparation_gate": "P2.1-R00",
  "ready_terminal": "READY",
  "blocked_terminals": ["HOLD", "ENVIRONMENT_FAILURE", "EVIDENCE_MISMATCH"],
  "formal_historical_result_count_max": 0,
  "legacy_phase2_write_allowed": false,
  "exit_codes": {
    "PASS": 0,
    "ENVIRONMENT_FAILURE": 3,
    "INVALID_CONTRACT": 4,
    "EVIDENCE_MISMATCH": 5,
    "HOLD": 20
  }
}
```

The readiness schema must require: release ID, contract identity, selected Git commit,
Phase 1 identities, preflight identity, wheelhouse identity, benchmark identity,
collection-rehearsal identity, role-boundary identity, artifact root, formal
historical result count, terminal and signature. Its artifact root pattern must
be `^artifacts/phase-2\\.1/phase2\\.1-[0-9]{8}-[A-Za-z0-9._-]+/$`.

- [ ] **Step 4: Run the schema test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_contract_schema -v
```

Expected: all contract-schema tests pass.

- [ ] **Step 5: Commit the contract boundary**

```bash
git add docs/roadmap/phase-2.1-acceptance-contract.json schemas/phase2_1/readiness.schema.json tests/phase2_1/test_contract_schema.py
git commit -m "feat: define phase 2.1 preparation contract"
```

### Task 2: Implement VPS Host and Source Preflight

**Files:**
- Create: `schemas/phase2_1/vps-preflight.schema.json`
- Create: `scripts/phase2_1/preflight_vps.py`
- Create: `tests/phase2_1/test_preflight_vps.py`
- Create: `tests/phase2_1/test_preflight_vps_cli.py`

- [ ] **Step 1: Write failing tests for baseline, source identity and isolation**

Cover a passing Linux/x86_64 fixture and failures for fewer than 4 CPUs, less
than 16 GiB RAM, less than 60 GiB free disk, a dirty checkout, missing Git LFS
object, unresolved Phase 1 input hash, and a release directory outside
`P2_VPS_ROOT/releases/<release-id>/`.

```python
def test_preflight_holds_when_disk_is_below_60_gib(self) -> None:
    facts = valid_host_facts(free_bytes=59 * 1024**3)
    report = evaluate_preflight(facts, valid_release_request())
    self.assertEqual(report["terminal"], "HOLD")
    self.assertIn("free_disk_bytes", report["failures"])
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_preflight_vps tests.phase2_1.test_preflight_vps_cli -v
```

Expected: failure because the collector and its schema do not exist.

- [ ] **Step 3: Implement a deterministic, read-only preflight collector**

`preflight_vps.py` must collect and serialize only:

- system name, kernel release, architecture, UTC timestamp, logical CPU count,
  total RAM bytes and free bytes in the release filesystem;
- exact `sys.version`, interpreter path and package list;
- resolved `git rev-parse HEAD`, clean-tree result and Git LFS verification;
- SHA-256 identities for the specified Phase 1 baseline files and Phase 2.1
  contract;
- release root, temporary root, checkpoint root and evidence root;
- an allowlisted preparation-network statement supplied by the operator, never a
  credential or command history.

It must take explicit `--repo-root`, `--vps-root`, `--release-id`,
`--contract` and `--output` arguments. It returns `3` for unusable host or
environment, `5` for source identity mismatch, `20` for incomplete readiness
evidence, and writes one canonical JSON report for every terminal state.

- [ ] **Step 4: Run tests and a local synthetic fixture**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_preflight_vps tests.phase2_1.test_preflight_vps_cli -v
python3 scripts/phase2_1/preflight_vps.py --help
```

Expected: tests pass and the help output lists only explicit paths and no
network-fetch option.

- [ ] **Step 5: Commit the preflight collector**

```bash
git add schemas/phase2_1/vps-preflight.schema.json scripts/phase2_1/preflight_vps.py tests/phase2_1/test_preflight_vps.py tests/phase2_1/test_preflight_vps_cli.py
git commit -m "feat: add phase 2.1 VPS preflight"
```

### Task 3: Build the Isolated Environment and Wheelhouse

**Files:**
- Create: `requirements/phase2.1.lock`
- Create: `scripts/phase2_1/build_wheelhouse.py`
- Create: `tests/phase2_1/test_build_wheelhouse.py`
- Modify: `schemas/phase2_1/vps-preflight.schema.json`

- [ ] **Step 1: Write failing wheelhouse-inventory tests**

Test that every resolved wheel has path, byte count and SHA-256; that duplicate
normalized package/version records fail; that no wheelhouse path escapes the
release root; and that the report records the exact CPython patch version.

```python
def test_inventory_rejects_missing_wheel_hash(self) -> None:
    payload = valid_wheelhouse_inventory()
    del payload["wheels"][0]["sha256"]
    self.assertFalse(validate_wheelhouse(payload).valid)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_build_wheelhouse -v
```

Expected: failure before the inventory builder and schema extension exist.

- [ ] **Step 3: Freeze and record dependencies**

Create `requirements/phase2.1.lock` from the Phase 2.1 implementation
environment. The builder must:

1. create a release-local virtual environment;
2. download the exact lock contents to
   `$P2_VPS_ROOT/releases/<release-id>/wheelhouse/`;
3. install only from that wheelhouse after download;
4. emit a canonical inventory of every wheel and installed package;
5. record the lock SHA-256, interpreter identity and installation transcript
   SHA-256.

The tool may use the public network only during step 2. It must not install a
dependency not present in the lock or fetch a package during its offline install
pass.

- [ ] **Step 4: Run unit tests and the offline-install contract test**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_build_wheelhouse -v
python3 scripts/phase2_1/build_wheelhouse.py --help
```

Expected: tests pass; help describes separate `prepare-download` and
`offline-install` modes.

- [ ] **Step 5: Commit the isolated environment workflow**

```bash
git add requirements/phase2.1.lock scripts/phase2_1/build_wheelhouse.py tests/phase2_1/test_build_wheelhouse.py schemas/phase2_1/vps-preflight.schema.json
git commit -m "feat: add phase 2.1 isolated environment workflow"
```

### Task 4: Bootstrap a Write-Isolated Release Workspace

**Files:**
- Create: `scripts/phase2_1/bootstrap_release.py`
- Create: `tests/phase2_1/test_bootstrap_release.py`
- Modify: `schemas/phase2_1/vps-preflight.schema.json`

- [ ] **Step 1: Write failing path-isolation tests**

The tests must verify that bootstrap creates only the five documented release
directories, rejects a release ID containing `/`, rejects a target outside
`P2_VPS_ROOT/releases/`, and rejects a legacy Phase 2 artifact root.

```python
def test_bootstrap_refuses_legacy_phase2_artifact_root(self) -> None:
    with self.assertRaises(ValueError):
        build_release_layout(root, "phase2.1-20260805-r00", Path("artifacts/phase-2"))
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_bootstrap_release -v
```

Expected: failure because no Phase 2.1 workspace builder exists.

- [ ] **Step 3: Implement bootstrap and write-policy verification**

The bootstrap tool must create exactly these release-local directories:
`repo`, `.venv`, `wheelhouse`, `work`, and `evidence`. It must record
their resolved paths and prove that:

- `work` and checkpoints are release-local;
- the repository checkout is at the selected commit and clean;
- legacy `artifacts/phase-2/` is not an output target;
- formal-result roots do not exist before `P2.1-R00 READY`.

The implementation must use `Path.resolve()` containment checks, not string
prefix checks.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_bootstrap_release -v
```

Expected: all path-escape, legacy-write and normal-layout tests pass.

- [ ] **Step 5: Commit workspace isolation**

```bash
git add scripts/phase2_1/bootstrap_release.py tests/phase2_1/test_bootstrap_release.py schemas/phase2_1/vps-preflight.schema.json
git commit -m "feat: isolate phase 2.1 release workspaces"
```

### Task 5: Benchmark the VPS and Derive the Resource Budget

**Files:**
- Create: `schemas/phase2_1/vps-benchmark.schema.json`
- Create: `scripts/phase2_1/benchmark_vps.py`
- Create: `tests/phase2_1/test_benchmark_vps.py`
- Modify: `docs/roadmap/phase-2.1-acceptance-contract.json`

- [ ] **Step 1: Write failing benchmark and forecast tests**

Test fixed synthetic-only input, deterministic seed handling, nonzero elapsed and
peak-memory metrics, and refusal to forecast a formal workload when benchmark
evidence is missing or from another release ID.

```python
def test_budget_rejects_benchmark_from_other_release(self) -> None:
    with self.assertRaises(ValueError):
        derive_budget(benchmark(release_id="phase2.1-20260805-a"), release_id="phase2.1-20260805-b", grid_points=240)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_benchmark_vps -v
```

Expected: failure before the benchmark and forecast implementation exist.

- [ ] **Step 3: Implement the synthetic benchmark contract**

The benchmark must use no Phase 1 historical draws and must record:

- synthetic scenario ID, fixed seed, worlds per scenario and repetitions;
- elapsed wall time, CPU count, peak process memory, output bytes and free disk;
- the exact release identity and wheelhouse identity;
- a conservative forecast for the registered simulation grid using the measured
  unit costs and an explicit safety factor.

The contract must reject a forecast exceeding the frozen wall-clock, memory or
storage budget. It must return `HOLD` rather than silently shrink a statistical
grid.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_benchmark_vps -v
```

Expected: all synthetic-only and release-identity tests pass.

- [ ] **Step 5: Commit benchmark support**

```bash
git add schemas/phase2_1/vps-benchmark.schema.json scripts/phase2_1/benchmark_vps.py tests/phase2_1/test_benchmark_vps.py docs/roadmap/phase-2.1-acceptance-contract.json
git commit -m "feat: benchmark phase 2.1 VPS capacity"
```

### Task 6: Collect and Verify Evidence Bundles

**Files:**
- Create: `schemas/phase2_1/evidence-bundle.schema.json`
- Create: `scripts/phase2_1/collect_evidence.py`
- Create: `tests/phase2_1/test_collect_evidence.py`
- Create: `tests/phase2_1/test_collect_evidence_cli.py`

- [ ] **Step 1: Write failing two-sided inventory tests**

Cover deterministic sorted inventory generation, a receiver-side byte change,
missing source file, path traversal, secret-like filenames, duplicate manifest
paths and mismatched release IDs.

```python
def test_receiver_rejects_any_sha256_mismatch(self) -> None:
    source = build_inventory(source_dir)
    (receiver_dir / "report.json").write_text("changed", encoding="utf-8")
    verdict = verify_receiver_inventory(source, receiver_dir)
    self.assertEqual(verdict["terminal"], "EVIDENCE_MISMATCH")
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_collect_evidence tests.phase2_1.test_collect_evidence_cli -v
```

Expected: failure because bundle collection is not yet implemented.

- [ ] **Step 3: Implement evidence collection**

The collector must build a sorted canonical inventory containing relative path,
byte count and SHA-256 for only allowlisted release evidence. It must reject:

- credentials, private keys, shell-history files and paths outside the release
  evidence root;
- temporary caches and raw checkpoints not selected for evidence;
- any path under `artifacts/phase-2/`;
- duplicate or unverified manifest entries.

The receiver verifier must recompute every file hash after transfer and write a
separate receipt. The source bundle, source inventory and receiver receipt form
the collection-rehearsal evidence required by `P2.1-R00`.

- [ ] **Step 4: Run tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_collect_evidence tests.phase2_1.test_collect_evidence_cli -v
```

Expected: complete deterministic inventory and mismatch-rejection coverage passes.

- [ ] **Step 5: Commit evidence collection**

```bash
git add schemas/phase2_1/evidence-bundle.schema.json scripts/phase2_1/collect_evidence.py tests/phase2_1/test_collect_evidence.py tests/phase2_1/test_collect_evidence_cli.py
git commit -m "feat: verify phase 2.1 evidence bundles"
```

### Task 7: Aggregate and Validate P2.1-R00 Readiness

**Files:**
- Create: `scripts/phase2_1/validate_phase2_1_readiness.py`
- Create: `tests/phase2_1/test_validate_phase2_1_readiness.py`
- Create: `artifacts/phase-2.1/readiness/.gitkeep`
- Modify: `docs/roadmap/phase-2.1-acceptance-contract.json`

- [ ] **Step 1: Write failing readiness state-machine tests**

Test `READY` only for fully consistent fixture evidence. Test `HOLD` for an
incomplete role assignment, unavailable network-policy record, failed benchmark
forecast or any formal-result count above zero. Test `EVIDENCE_MISMATCH` for
a changed identity and `INVALID_CONTRACT` for an invalid readiness document.

```python
def test_any_formal_result_blocks_r00_ready(self) -> None:
    evidence = valid_evidence_set(formal_historical_result_count=1)
    payload, exit_code = validate_readiness(evidence)
    self.assertEqual(exit_code, HOLD)
    self.assertEqual(payload["terminal"], "HOLD")
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_validate_phase2_1_readiness -v
```

Expected: failure because the aggregate validator does not exist.

- [ ] **Step 3: Implement the aggregate validator**

The validator must read only explicit paths named in the contract. It recomputes
every identity, confirms release-ID equality across all reports, checks the
resource floor, requires the benchmark-derived forecast, confirms the old Phase
2 artifact root was never selected for output, and requires formal historical
result count to equal zero. It writes one canonical result at:

```text
artifacts/phase-2.1/readiness/<release-id>/p2.1-r00-readiness.json
```

It must not mutate evidence inputs, choose a newest run, or transform a failed
terminal into `READY`.

- [ ] **Step 4: Run all preparation tests**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
```

Expected: all Phase 2.1 preparation tests pass.

- [ ] **Step 5: Commit the readiness gate**

```bash
git add scripts/phase2_1/validate_phase2_1_readiness.py tests/phase2_1/test_validate_phase2_1_readiness.py artifacts/phase-2.1/readiness/.gitkeep docs/roadmap/phase-2.1-acceptance-contract.json
git commit -m "feat: validate phase 2.1 VPS readiness"
```

### Task 8: Run the First VPS Rehearsal and Publish the Operator Runbook

**Files:**
- Create: `docs/runbooks/phase-2.1-vps-operator.md`
- Create: `artifacts/phase-2.1/readiness/<release-id>/p2.1-r00-readiness.json`
- Create: `artifacts/phase-2.1/readiness/<release-id>/preflight.json`
- Create: `artifacts/phase-2.1/readiness/<release-id>/benchmark.json`
- Create: `artifacts/phase-2.1/readiness/<release-id>/collection-receipt.json`
- Modify: `docs/handoff/phase-2-current-status.md`
- Modify: `README.md`

- [ ] **Step 1: Write a dry-run test for the documented operator command sequence**

The test invokes the command sequence against a temporary release root and
asserts that all written files are under that root, the formal result count is
zero, and the final terminal is `READY`.

```python
def test_operator_rehearsal_reaches_ready_without_formal_results(self) -> None:
    result = run_rehearsal(temp_release_root())
    self.assertEqual(result["terminal"], "READY")
    self.assertEqual(result["formal_historical_result_count"], 0)
```

- [ ] **Step 2: Run the test and confirm it fails before the runbook and rehearsal exist**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.phase2_1.test_operator_rehearsal -v
```

Expected: failure until the helper command sequence and fixtures exist.

- [ ] **Step 3: Write the operator runbook and execute the remote rehearsal**

The runbook must contain these explicit stages, each with expected terminal
state and retained output path:

1. provision or inspect the Linux/x86_64 VPS;
2. export a release-local `P2_VPS_ROOT` and selected release ID;
3. clone the exact commit and fetch Git LFS while preparation networking is
   enabled;
4. build the wheelhouse and perform the offline install;
5. run preflight, bootstrap, synthetic benchmark and budget derivation;
6. create and verify a dry-run evidence bundle at the receiving checkout;
7. run the aggregate validator;
8. stop on any nonzero, `HOLD`, `ENVIRONMENT_FAILURE` or
   `EVIDENCE_MISMATCH` terminal.

The remote rehearsal must not invoke `audit`, `power`, `replay` or
`accept`. It must use a new release ID and preserve all reports.

- [ ] **Step 4: Update project entry points**

Update the handoff document to link the design and this plan under the Phase
2.1 continuation path. Update the README to state that remote VPS readiness is
a Phase 2.1 prerequisite and that local test success does not substitute for
`P2.1-R00 READY`.

- [ ] **Step 5: Run the complete verification set**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p "test_*.py" -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2_readiness -p "test_*.py" -v
git diff --check
```

Expected: all new preparation tests, existing Phase 2 regression tests and
legacy readiness tests pass; the rehearsal evidence says `READY` with zero
formal historical results; the working tree has no whitespace errors.

- [ ] **Step 6: Commit the readiness evidence and operator entry points**

```bash
git add docs/runbooks/phase-2.1-vps-operator.md docs/handoff/phase-2-current-status.md README.md artifacts/phase-2.1/readiness tests/phase2_1/test_operator_rehearsal.py
git commit -m "docs: publish phase 2.1 VPS readiness runbook"
```

## Historical Plan-Level Acceptance

This preparation plan was complete when the VPS produced a verifiable
`P2.1-R00 READY` artifact, the receiver-side evidence receipt had zero
mismatches, and no Phase 2.1 formal historical result existed. Those conditions
were satisfied before the separately approved statistical implementation and
formal release. The later accepted `P2.1-R00-61a99a2c3732-i07-r02` bundle is now
the terminal Phase 2 authority.
