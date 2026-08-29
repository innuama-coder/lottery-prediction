# Round 04 test-skip audit

No skip was added or broadened by Round 04.

The Phase 4 suite contains 20 `skipUnless(LEGACY_PREP_INSTALLED, ...)`
decorators.  Each names unavailable superseded T00–T24 evidence (calendar,
source-review, forecast/probability/metric oracles, installed state,
actor-assignment, research, or correction receipts).  They remain diagnostic
legacy cases and are not used by the D00–D15 P4E2 acceptance path.

Two pre-existing platform guards are present.  On this Linux/POSIX execution
environment their conditions are satisfied, so the trainer-isolation and
calendar-runtime cases run.  The dynamic `tzset unavailable` branch is not
taken on this host.  P4E2, D00–D15, lifecycle, lock, exact score, research,
replay, probability, ledger, recovery, mutation, and final-acceptance tests
have no skip decorator and must execute in the formal matrix.

