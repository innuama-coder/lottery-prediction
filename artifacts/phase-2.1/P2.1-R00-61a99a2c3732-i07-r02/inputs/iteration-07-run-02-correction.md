# Run 02 correction: valid adversarial baseline is mandatory

Run 01 is failed evidence. Do not delete, edit, or overwrite it. It created an
untracked test but no production changes; preserve the worktree state and either
replace that invalid test or amend it only after saving a factual note of why it
was invalid. The run ended through a Codex stdout broken-pipe panic (`exit 101`),
not after a completed implementation.

## Root-cause finding

The first attempted "red" tests were not valid regression tests. Their fixture
was rejected for `formal command receipt coverage mismatch` before either the
E2E, canonical-command, or late-result closure being exercised. A test that
asserts merely that *some* exception is raised cannot prove a review finding and
must not be retained as evidence.

## Required test-first proof, in this exact order

For **each** of P1-A, P1-B, and P1-C, before changing any production file:

1. Construct a fixture from a completed valid bundle that passes
   `validate_final_bundle()` on the unmodified rejected-head implementation.
2. Apply exactly the owner-reviewed mutation and rebuild only artifacts that an
   attacker can self-consistently rebuild (affected identities, recursive
   manifest, and acceptance). Do not conceal the attack by deleting files.
3. Run the old final validator and record whether it accepts; if it rejects,
   the rejection must demonstrably be because of the missing security closure
   under investigation, not an unrelated Schema, inventory, receipt-coverage,
   or fixture setup failure. If an older protection already rejects a mutation,
   state precisely which protection and choose the reviewer-equivalent mutation
   that reaches the uncovered closure instead of writing a broad `assertRaises`.
4. Write the permanent test with a specific expected outcome/message. Observe
   it fail for the missing closure. Only then implement the smallest production
   change that makes the test pass by rejecting the forged artifact for the
   intended reason.

The three mandatory attacks are:

- P1-A: mutate `E2E-P2.1-03-release-mismatch` to expected/observed/terminal
  `PASS`, alter its receipt/evidence as the comment permits, rebuild mutable
  artifacts, and prove frozen canonical semantics cause final rejection. Also
  test each negative canonical E2E case with a forged zero exit receipt.
- P1-B: replace `logs/external-01.json` command with literal `true`, synchronize
  the run summary and mutable artifacts, and prove final rejection is due to
  canonical command identity/order/scope enforcement.
- P1-C: add a current-release `late-power.json` below the recorded task-input
  result root after readiness, rebuild only mutable artifacts, and prove final
  rejection occurs because it invokes the read-only formal-history rescan. The
  test must establish the read-only rescan itself has no candidate-bundle
  mutation side effects.

Do not use `assertRaises(Exception)`, a generic message assertion, or a fixture
that cannot first produce a passing valid bundle. Capture the observed RED and
GREEN command output paths in the final handoff.

## Scope and completion remain unchanged

The original i07 prompt remains fully binding, including all P1 requirements,
the complete G0-G6 run, all ten E2E cases, public E2E rerun, historical-input
protection, no resource thresholds, and the final remote-only validation. Do
not push until the parent independently reviews the full result.
