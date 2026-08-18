# Round 10 pre-allocation results

Status: `LINUX_PASS_MACOS_PREFLIGHT_REQUIRED`

Authority commit: `2f724053ba20b31aee952055c28dc006aea94a19`.

Implementation commit: `f906dde39db3e2c0f9556dd65d83dcd122da45f2`.

Focused Linux CPython 3.12.3 results:

- local verifier focused suite: 19 PASS;
- contract/schema validator: PASS, 12 schemas and 31 negative cases;
- primary and independent authority checkers: PASS;
- compileall: PASS;
- r11 and Phase 0-3 byte-diff gates: PASS.

Clean-tree full Linux pre-allocation audit:

- exact r11 inventory: 178 files,
  `b01b69df6f5a39fab7b2b2215f6a89306606d6f96f354711aceaf894464357d9`;
- independent semantic comparisons: SSQ 54,807; DLT 54,865;
- numeric comparisons: 124,880;
- configured/observed patterns: 86/86;
- profile comparisons: tight 105,159; feature/context 5,811; nested number
  feature 1,440; coefficient 224; propagated zone score 6,246; display
  probability 6,000;
- new bound failures: 0;
- all six profiles passed the ULP boundary and rejected next ULP,
  just-outside absolute, relative subnormal, and non-finite values;
- exact checks retained, product-core imports 0, r11 unchanged.

Create-once evidence: `linux-full-numeric-preflight.json`, SHA-256
`7152b5eda8208070f1768e11a23eb3c7ded641eef12f67fd9a74c89697a49923`.

No `r12` or other successor identity exists. The next gate is the exact macOS
CPython 3.12.11 preflight.
