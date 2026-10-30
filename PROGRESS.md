# Progress

## Last session

- Date: 2026-10-30
- Story: S0.1 follow-up - feature list and guard
- Summary: Added `feature_list.json` tests (`tests/test_feature_list.py`) and a pre-commit guard
  (`scripts/check_feature_list.py`) that allows only false->true `passes` changes versus HEAD.
  `scripts/verify.py` passes locally on Windows.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook

## Next

- S0.2: settings from `.env` with Pydantic (next feature with passes=false, in list order).

## Open issues

- CI workflow not yet run on GitHub.
- The guard is a local pre-commit hook only; `git commit --no-verify` skips it. No CI check by choice.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
- Broken uv junction at `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (harmless warning).
