# Round 08 pre-allocation gates

No round-08 release identity existed while these gates ran. The complete gate
set passed against source commit `424be2dfe3b3d307aadd1a202f17cc9d2155cf6e`
plus the create-once evidence file produced by the final preserved-r10 audit.

| Gate | Runtime | Result |
|---|---|---|
| focused stable-key and local-verifier tests | Linux CPython 3.12 | 20 PASS |
| complete Phase 4 matrix | Linux CPython 3.12 | 161 PASS, 20 audited superseded-only skips, 3610.137 s |
| Phase 4 independent Oracle matrix | Linux CPython 3.12 | 18 PASS, 0.195 s |
| Phase 3 matrix | Linux CPython 3.12 | 69 PASS, 199.697 s |
| Phase 2.1 historical matrix | frozen CPython 3.12.3 | 39 PASS, 129.135 s |
| Phase 2 historical matrix | frozen CPython 3.12.3 | 31 PASS, 4.071 s |
| primary and independent D00 authority checkers | Linux CPython 3.12 | PASS; authority commit changes exactly four authority files |
| Phase 4 contract/schema validator | Linux CPython 3.12 | PASS; 12 schemas, 31 negative cases |
| compileall | Linux CPython 3.12 | PASS |
| prohibited active binary64-identity identifier audit | repository active surfaces | PASS; zero matches |
| preserved r10 byte-diff gate | git tree at round-07 commit | PASS; zero changes |
| preserved r10 independent stable-key migration replay | Linux CPython 3.12 | PASS; 6 scopes, 6,000 rows, 12,000 semantic numeric comparisons, product core imports 0 |

The preserved-r10 replay proves:

- release file count remains 178 and aggregate inventory remains
  `e3b65e2ef7c7ab12ee7fe21c68d9847858661446f1a5242528db0dc46ba19d5c`;
- every observed row and one `nextafter` step in either direction has the same
  `P4S10HE1` identity;
- product and independent implementations agree for every row and the six
  controller fixtures;
- every adjacent distinct score remains distinct, with global minimum gap
  `4.326295779955025012e-10`;
- canonical ticket membership, order, and rank are unchanged in all six scopes;
- the 17-ULP probability profile and 151-ULP feature profile remain unchanged;
- stable key, score identity, tie key/group, probability layer, tie bounds, and
  tie ordering remain exact integrity fields and are not tolerance-routed.

These results release the round-08 pre-allocation hold. The next available
identity may now be allocated; no earlier release may be modified or reused.
