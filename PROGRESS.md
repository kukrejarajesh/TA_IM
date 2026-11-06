# Progress

## Last session

- Date: 2026-11-06
- Story: S1.2 - daily OHLCV for .NS tickers via yfinance, cached locally
- Summary: `PriceBar` and `PriceCache` (`price_cache.py`, one JSON file per ticker under
  `Settings.data_cache_dir`, default `.data_cache`), `PriceProvider` and `yfinance_fetcher` (`prices.py`).
  The provider takes an injected fetcher, so tests are offline. It requires a `.NS` ticker and tz-aware
  `as_of`, filters vendor over-returns (logged), filters again on every cache read, and ends with
  `guard_records`. The cache tracks the fetched range `[start, through]` and fetches only the missing head
  or tail. Holidays are absent, never invented; NaN and zero-volume rows are dropped. Decision: a day's bar
  is visible only from 15:30 IST that day (`MARKET_CLOSE`); before that, the previous day is the last bar.
  `DataProvider.prices` now returns `Sequence[PriceBar]`. Default lookback is 400 calendar days.
  12 tests in `tests/data/test_prices.py` plus 1 `live` smoke test (not run).
- Extension (branch `s1.2-kite-fetcher`, not a listed story): hard rule 2 relaxed in AGENTS.md (broker API
  clients allowed for market data; no real orders). Added `ta_in.data.kite` (`make_kite_fetcher`,
  `kite_client_from_settings`, `KiteConfigError`) as a second `PriceProvider` fetcher over Kite Connect, with
  optional `TA_KITE_API_KEY` / `TA_KITE_ACCESS_TOKEN` settings. The price cache is now per source
  (`prices/<source>/<ticker>.json`, `PriceProvider(..., source="kite")`). 7 offline tests in
  `tests/data/test_kite_prices.py`; the Kite live test is skipped without credentials.
- Earlier: S1.1 added `DataProvider` (Protocol) and `point_in_time.py` (`IST`, `PointInTimeError`,
  `is_visible`, `check_not_after`, `guard_records`). The guard raises and never filters.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (merged to main, PR #2; default budget $1)
- S0.3: LLM client wrapper with retry, cost records and budget stop (merged, PR #3)
- S0.4: response cache with record and replay (merged, PR #4)
- S0.5: run trace and trace-summary (merged, PR #5)
- S0.8: shared decision models (merged, PRs #6 and #7)
- S1.1: DataProvider interface + PointInTimeError guard (merged, PR #8)
- S1.2: daily OHLCV for .NS tickers (branch `s1.2-prices`, PR pending)

## Next

- S1.6 (identity) is next in list order; S1.3, S1.7, S1.4 and S1.5 are also unblocked.
  Each provider must refine the `DataProvider` return types and pass results through `guard_records`.
- S0.7 (baseline run) is blocked until S1.8 passes; it also needs the fixture set, which is still undecided.

## Open issues

- The S1.2 yfinance live test failed on 2026-11-06 with a connect timeout to Yahoo from the dev machine; run it
  where Yahoo is reachable. The Kite live test has never been run (needs credentials and a paid history plan).
- Kite history has no adjusted close (`adj_close == close`); split/bonus adjustment is not verified.

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
