# Phase 3 review fixes R04: Git-free acceptance fixture

The Phase 3 review implementation at this branch is complete, but independent
acceptance checks out source files without `.git`. The Phase 3 suite currently
fails there because `tests/phase3/_release_fixture.py` executes
`git rev-parse HEAD` while building a synthetic authorized release.

Fix this test-fixture defect. This is a narrow acceptance-portability task, not
a redesign of Phase 3.

## Required change

- Modify only `tests/phase3/_release_fixture.py`.
- Remove the fixture's dependency on Git metadata and Git executables.
- Derive the synthetic `implementation_freeze_commit` deterministically from
  the fixture's actual implementation inventory. A stable 40-character
  lowercase hexadecimal identity derived from the canonical inventory hash is
  acceptable.
- Use the same derived identity in `implementation-inventory.json`,
  `formal-authorization.json`, and `release-control.json`.
- Keep production code unchanged. Production W07/readiness must continue to
  bind formal releases to the real Git commit.
- Keep all model, feature, data, statistical, threshold, Champion, and PIT
  contracts unchanged.
- Do not create or modify any file under `artifacts/`.

## Verification

Run all commands below. The first focused check must prove the fixture works
when the source tree is copied without `.git`.

```sh
python3 -m compileall -q -f tests/phase3/_release_fixture.py
tmp_root=$(mktemp -d)
tar --exclude=.git -cf - . | tar -xf - -C "$tmp_root"
(cd "$tmp_root" && PYTHONPATH=src python3 -m unittest discover -s tests/phase3 -p 'test_*.py' -v)
rm -rf "$tmp_root"
TMPDIR=/tmp PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p 'test_*.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p 'test_*.py' -v
```

The copied-tree command is for provider self-checking only. The independent
verifier already runs from a checkout without `.git` and will execute the same
three test suites directly.

## Completion

After verification, commit the single-file change:

```sh
git add tests/phase3/_release_fixture.py
git commit -m "test: make phase3 release fixture git independent"
```

The final worktree must be clean. Report the commit SHA and test counts.
