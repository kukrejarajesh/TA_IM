# Progress

## Last session

- Date: 2026-10-30
- Story: S0.1 - repo skeleton with harness
- Summary: uv project, src/ta_in package, verify/init scripts with sh/ps1 wrappers,
  pre-commit, AGENTS.md/CLAUDE.md, .claude rules and settings, CI (ubuntu 3.11/3.12, windows 3.12).
  `scripts/verify.py` passes locally on Windows.

## Done

- S0.1: repo skeleton with harness

## Next

- Create `feature_list.json` (right after S0.1), then pick the first feature with passes=false.

## Open issues

- CI workflow not yet run on GitHub.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
