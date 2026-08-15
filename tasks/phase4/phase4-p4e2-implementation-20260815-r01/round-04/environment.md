# Round 04 environment

Status: `PASS`

- worktree: `/home/royzuo/worktrees/lottery-prediction-phase4-p4e2-implementation-20260815-r01`
- branch: `codex/phase4-p4e2-implementation-20260815-r01`
- authority: `95f3c6cbe1a43c7ed390907795cc89f4a64b51a9`
- authority freeze: `e142773ddbf24f3890bb2fcd9028d7d5ea754250`
- implementation commit: `7c14eb0ee7f9aa803a5bcd684aaecd5fa35d42b6`
- Phase 4 interpreter: `/usr/bin/python3.12`
- historical Phase 2/2.1 interpreter: `/home/royzuo/codex-tasks/phase4-p4e2-implementation-20260815-r01/acceptance-venv/bin/python`
- historical interpreter base realpath: `/usr/bin/python3.12`
- historical environment: installed only inside this task directory from an existing offline wheelhouse with `--no-index`
- NumPy: `2.5.1`; wheel SHA-256: `59fda5e192b570217ec2580c96f00e9a7e12ef6866a900eb089b62c1a32545ca`
- `requirements/phase4.lock` SHA-256: `13ee8e8d10c675d1b9ebe6bad1a41d3a8bd0600657857eab2dcf2af2032005bf`
- maximum observed Phase 4 test RSS: `363780 KiB`
- sudo, host configuration, services, firewall, accounts, system packages, production operations: not used

The r04 generated runbook freezes both interpreter invocation paths and records
the historical base realpath. It contains no unresolved interpreter template
and does not reference the invalid `.venv-phase4/bin/python` path.
