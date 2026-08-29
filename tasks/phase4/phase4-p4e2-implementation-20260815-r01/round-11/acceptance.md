# Round 11 acceptance

Status: `PASS_LINUX_READY_FOR_FINAL_MACOS_CONTROLLER`

Release `P4-P4E2-20260815-r12` is uniquely allocated, fully closed, and Linux
accepted under `P4-LOCAL-PATH-CLASSIFIED-BINARY64-5`.

- final state: `READY_FOR_LOCAL_PRODUCT_ACCEPTANCE`;
- manifest coverage: 174 entries, 1.0, including D14;
- independent replay/mutations: 100% / 100% (28/28);
- all required A01-A10 receipts plus A07b: exit 0;
- exact final inventory: 178 files;
- Linux public local command: PASS and release unchanged;
- r11, earlier releases, adverse evidence, and Phase 0-3: unchanged.

Remaining gate: the independent controller must run `controller-command.md` on
macOS CPython 3.12.11. No final macOS r12 PASS is claimed here.
