# Progress

## Last session

- Date: 2026-10-31
- Story: S0.4 - response cache with record and replay
- Summary: `ta_in.llm.cache` (`cache_key`, `ResponseCache`, `CacheMode` off/record/replay, `CacheMiss`,
  `CacheError`). `LLMClient` replays from disk without building an SDK client; replayed calls are
  `cached=True` and cost 0. Settings `TA_CACHE_MODE` / `TA_CACHE_DIR`. 19 cache tests in
  `tests/llm/test_cache.py`; LLM test fakes moved to `tests/llm/helpers.py`. `scripts/verify.py` passes.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (merged to main, PR #2; default budget $1)
- S0.3: LLM client wrapper with retry, cost records and budget stop (merged, PR #3)
- S0.4: response cache with record and replay (branch `s0.4-response-cache`, PR pending)

## Next

- S0.5: run trace (next in list order; S0.4 was done ahead of it at the user's request).

## Open issues

- The guard is a local pre-commit hook only; `git commit --no-verify` skips it. No CI check by choice.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
- Broken uv junction at `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (harmless warning).
- Default model IDs in `Settings` are provisional.
- `Settings.temperature` is not sent to the API: sampling params are rejected on current models (400).
- `pricing.py` prices are first-party list rates as of 2026-10; cache-read/creation tokens not costed yet.
- Cache stores response text and token counts only; tool-use responses are not cached yet.
