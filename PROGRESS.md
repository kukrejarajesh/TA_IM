# Progress

## Last session

- Date: 2026-10-31
- Story: S0.3 - LLM client wrapper
- Summary: `ta_in.llm` with `LLMClient` (own retry/backoff, SDK `max_retries=0`), `CallRecord` per call,
  `BudgetExceeded` on breach, `pricing.py` per-model prices. 11 offline tests in `tests/llm/test_client.py`.
  `scripts/verify.py` passes locally. PROGRESS.md S0.2 update folded into this branch.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (merged to main, PR #2; default budget $1)
- S0.3: LLM client wrapper with retry, cost records and budget stop (branch `s0.3-llm-client`, PR pending)

## Next

- S0.4 (next feature with passes=false in `feature_list.json`).

## Open issues

- The guard is a local pre-commit hook only; `git commit --no-verify` skips it. No CI check by choice.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
- Broken uv junction at `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (harmless warning).
- Default model IDs in `Settings` are provisional.
- `Settings.temperature` is not sent to the API: sampling params are rejected on current models (400).
- `pricing.py` prices are first-party list rates as of 2026-10; cache-read/creation tokens not costed yet.
