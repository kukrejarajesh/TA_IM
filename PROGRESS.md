# Progress

## Last session

- Date: 2026-11-02
- Story: S1.1 - DataProvider interface + PointInTimeError guard (first story of E1)
- Summary: `ta_in.data` with `DataProvider` (Protocol; `prices`, `news`, `fundamentals`, `identity`, each
  with a required keyword-only `as_of`) and `point_in_time.py` (`IST`, `PointInTimeError`, `is_visible`,
  `check_not_after`, `guard_records`). Rules: `as_of` must be tz-aware (else `ValueError`); `datetime`
  records compare as instants (naive read as IST); `date` records are visible through the IST calendar
  date of `as_of`, inclusive. IST is a fixed +05:30 offset (no `tzdata` dependency). The guard raises and
  never filters; filter-and-log is up to each provider. Protocol return types are loose (`Any`) until
  S1.2-S1.6 add concrete record types. 16 tests in `tests/data/test_point_in_time.py`.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (merged to main, PR #2; default budget $1)
- S0.3: LLM client wrapper with retry, cost records and budget stop (merged, PR #3)
- S0.4: response cache with record and replay (merged, PR #4)
- S0.5: run trace and trace-summary (merged, PR #5)
- S0.8: shared decision models (merged, PRs #6 and #7)
- S1.1: DataProvider interface + PointInTimeError guard (branch `s1.1-point-in-time`, PR pending)

## Next

- S1.2: daily OHLCV for .NS tickers (next in list order); S1.6, S1.4 and S1.5 also unblocked.
  Each provider must refine the `DataProvider` return types and pass results through `guard_records`.
- S0.7 (baseline run) is blocked until S1.8 passes; it also needs the fixture set, which is still undecided.

## Open issues

- The guard is a local pre-commit hook only; `git commit --no-verify` skips it. No CI check by choice.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
- Broken uv junction at `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (harmless warning).
- Default model IDs in `Settings` are provisional.
- `Settings.temperature` is not sent to the API: sampling params are rejected on current models (400).
- `pricing.py` prices are first-party list rates as of 2026-10; cache-read/creation tokens not costed yet.
- Cache stores response text and token counts only; tool-use responses are not cached yet.
- Trace `tool_calls` is always `[]` until `complete()` returns tool-use blocks. Trace time is summed call latency, not wall-clock.
- Not ported from upstream yet: `SentimentBand`/`SentimentReport`, `render_*` helpers, `extract_rating`/`parse_rating` (in local `docs/`).
- Upstream licence not recorded in the source comment; confirm the licence text before redistributing.
