# Round 09 pre-allocation results

Status: `LINUX_PASS_MACOS_PREFLIGHT_REQUIRED`

Implementation commit: `af28ed7de45e75d139b1050454ff7acf1546397f`

Focused Linux CPython 3.12.3 results:

- local verifier focused suite: 17 PASS;
- contract/schema validator: PASS, 12 schemas and 31 negative cases;
- primary and independent authority checkers: PASS;
- compileall: PASS;
- r11 and Phase 0-3 git byte-diff gates: PASS.

Clean-tree full Linux numeric migration preflight:

- exact r11 inventory: 178 files,
  `b01b69df6f5a39fab7b2b2215f6a89306606d6f96f354711aceaf894464357d9`;
- independent replay semantic comparisons: SSQ 54,807; DLT 54,865;
- full numeric comparisons: 124,880;
- configured/observed patterns: 86/86;
- profile comparisons: coefficient 224, feature/context 7,251, tight
  111,405, display probability 6,000;
- new bound failures: 0;
- every profile ULP boundary passed; next ULP, just-outside absolute,
  relative subnormal, and all non-finite negatives failed;
- product-core imports: 0;
- exact structure/identity checks retained: yes;
- r11 unchanged after replay: yes.

Create-once evidence:
`linux-full-numeric-preflight.json`, SHA-256
`df57e6de52c9aa954e3ef25dfa953a9d17899ff6cc664668c2c20cba43590885`.

This Linux result does not release pre-allocation. The next gate is the exact
macOS CPython 3.12.11 preflight. No `r12` or other successor identity exists.
