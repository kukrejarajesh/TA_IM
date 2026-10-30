# Progress

## Last session

- Date: 2026-10-31
- Story: S0.5 - run trace
- Summary: `ta_in.llm.trace` (`TraceWriter` = one JSONL file per run, `summarize`, `main`). `LLMClient`
  takes `trace=` and `complete(..., agent=)`; errors and budget breaches are traced too.
  `uv run trace-summary <run-id-or-path>` prints calls, tokens, cost and time per agent.
  16 tests in `tests/llm/test_trace.py`. `scripts/verify.py` passes.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (merged to main, PR #2; default budget $1)
- S0.3: LLM client wrapper with retry, cost records and budget stop (merged, PR #3)
- S0.4: response cache with record and replay (merged, PR #4)
- S0.5: run trace and trace-summary (branch `s0.5-run-trace`, PR pending)

## Next

- S0.8 (next feature with passes=false in list order; check `depends_on` first).

## Open issues

- The guard is a local pre-commit hook only; `git commit --no-verify` skips it. No CI check by choice.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
- Broken uv junction at `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (harmless warning).
- Default model IDs in `Settings` are provisional.
- `Settings.temperature` is not sent to the API: sampling params are rejected on current models (400).
- `pricing.py` prices are first-party list rates as of 2026-10; cache-read/creation tokens not costed yet.
- Cache stores response text and token counts only; tool-use responses are not cached yet.
- Trace `tool_calls` is always `[]` until `complete()` returns tool-use blocks. Trace time is summed call latency, not wall-clock.
