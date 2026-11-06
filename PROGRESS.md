# Progress

## Last session

- Date: 2026-11-06
- Story: S1.3 - indicators from prices (RSI, MACD, Bollinger, ATR, SMA, EMA, VWMA)
- Summary: `ta_in.data.indicators` with `compute_indicator(bars, kind, *, as_of, window=None)`,
  `IndicatorValue(date, value | None)` and `INDICATOR_KINDS` (`sma`, `ema`, `rsi`, `macd`, `macd_signal`,
  `macd_hist`, `boll_mid`, `boll_upper`, `boll_lower`, `atr`, `vwma`). Built on `stockstats` 0.6.10 (what upstream
  uses; works on pandas 3.1.1). Bars go through `guard_records` (a later bar raises `PointInTimeError`, nothing is
  filtered) and must be strictly ascending by date. Defaults: rsi 14, boll 20, atr 14, vwma 20; `sma`/`ema`
  need a window; MACD is fixed at 12/26/9 and rejects a window. Conventions are stockstats': EMA/RSI/ATR use
  `adjust=True` weights (RSI/ATR alpha = 1/window), Bollinger uses the sample std (ddof=1), VWMA weights the
  typical price (H+L+C)/3 by volume. stockstats pads the start with partial-window values; we replace those
  with `None` (sma/boll/vwma/atr: first window-1 bars, rsi: first window bars). EMA and MACD are not masked.
  16 offline tests in `tests/data/test_indicators.py`; expected values were worked out with plain arithmetic
  (and cross-checked with a separate plain-Python reference), not copied from the library.
- Previous story: S1.6 - company identity resolved from the ticker and cached
- S1.6 summary: `ta_in.data.identity` with `CompanyIdentity` (frozen: ticker, name, `sector | None`, exchange; no
  timestamp), `UnknownTickerError` (a `ValueError`), `IdentityProvider(resolver, cache_dir)` and
  `load_sector_map`. Cache: `<data_cache_dir>/identity/<TICKER>.json`; failures are never cached; a corrupt
  file is re-resolved. Tickers are stripped and upper-cased, so spelling cannot change the identity. The Kite
  resolver (`make_kite_identity_resolver` in `kite.py`) takes name and exchange from `instruments("NSE")`
  (equity rows only, lowest token wins on a repeat) and sector from `src/ta_in/data/sector_map.csv`
  (`symbol,sector`; `None` if the symbol is missing). Kite has no sector field. `DataProvider.identity` now
  returns `CompanyIdentity`. 14 offline tests in `tests/data/test_identity.py`; no live test, by choice.
  Limitation: identity is static, `as_of` is validated but never changes the answer (renames and sector
  changes over time are not modelled). `propagate` (S4.5) must resolve identity first so a bad ticker stops
  the run before any agent starts.
- Previous story: S1.2 - daily OHLCV for .NS tickers via yfinance, cached locally
- S1.2 summary: `PriceBar` and `PriceCache` (`price_cache.py`, one JSON file per ticker under
  `Settings.data_cache_dir`, default `.data_cache`), `PriceProvider` and `yfinance_fetcher` (`prices.py`).
  The provider takes an injected fetcher, so tests are offline. It requires a `.NS` ticker and tz-aware
  `as_of`, filters vendor over-returns (logged), filters again on every cache read, and ends with
  `guard_records`. The cache tracks the fetched range `[start, through]` and fetches only the missing head
  or tail. Holidays are absent, never invented; NaN and zero-volume rows are dropped. Decision: a day's bar
  is visible only from 15:30 IST that day (`MARKET_CLOSE`); before that, the previous day is the last bar.
  `DataProvider.prices` now returns `Sequence[PriceBar]`. Default lookback is 400 calendar days.
  12 tests in `tests/data/test_prices.py` plus 1 `live` smoke test (not run).
- Extension (merged to main, not a listed story): hard rule 2 relaxed in AGENTS.md (broker API
  clients allowed for market data; no real orders). Added `ta_in.data.kite` (`make_kite_fetcher`,
  `kite_client_from_settings`, `KiteConfigError`) as a second `PriceProvider` fetcher over Kite Connect, with
  optional `TA_KITE_API_KEY` / `TA_KITE_ACCESS_TOKEN` settings. The price cache is now per source
  (`prices/<source>/<ticker>.json`, `PriceProvider(..., source="kite")`). 7 offline tests in
  `tests/data/test_kite_prices.py`.
- Kite checked by hand on 2026-11-06 (not by a test): RELIANCE.NS with `as_of` 2025-06-30 16:00 IST gave 21
  daily bars, 2025-06-02 to 2025-06-30, none later than `as_of`. Needed the paid Historical API add-on and a
  fresh access token after enabling it.
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
- S1.2: daily OHLCV for .NS tickers via yfinance (merged to main), plus the Kite fetcher extension
- S1.6: company identity from Kite, cached (merged, PR #13)
- S1.3: indicators via stockstats (branch `s1.3-indicators`, not pushed)

## Next

- Sprint 2 is complete. Sprint 3 remaining: S1.4 news (needs a source decision; Kite has no news), S1.5
  fundamentals (Kite has no statements; yfinance was unreachable from the dev machine), S1.7 price snapshot
  (no new source needed, and S1.8 and S2.1 depend on it). Each provider must refine the `DataProvider` return types and pass results through
  `guard_records`.
- S0.7 (baseline run) is blocked until S1.8 passes; it also needs the fixture set, which is still undecided.

## Open issues

- The S1.2 yfinance live test (`test_live_yfinance_smoke`) failed on 2026-11-06 with a connect timeout to Yahoo
  from the dev machine and has never passed. Run `uv run pytest -m live tests/data/test_prices.py` where Yahoo
  is reachable.
- The Kite live test (`test_live_kite_smoke`) is skipped on purpose: it needs `TA_KITE_API_KEY` and
  `TA_KITE_ACCESS_TOKEN` exported in the shell, and the owner does not want secrets passed to test runs. It
  ignores `.env` (`Settings(_env_file=None)`). Kite was only checked by hand (see Last session).
- Kite access tokens expire daily and history needs the paid Historical API add-on.
- Kite history has no adjusted close (`adj_close == close`); split/bonus adjustment is not verified, and
  prices were not compared with another source.
- `src/ta_in/data/sector_map.csv` is a 5-symbol seed (RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK) and must be
  extended by hand for every fixture ticker; missing symbols get `sector=None`. Sector labels are unchecked.
- `ANTHROPIC_API_KEY` in the local `.env` is empty, so `load_settings()` fails until it is filled in.
- Local branches `s0.8-decision-types`, `s1.1-point-in-time`, `s1.2-prices`, `s1.2-kite-fetcher` are merged and
  can be deleted.

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
