# Progress

## Last session

- Date: 2026-11-12
- Story: S2.2 - generic agent loop with tool calling (merged, PR #23, CI green; `passes=true`).
  Two parts. **LLM layer, backward compatible:** `LLMClient.complete_turn(...)` returns an `LLMTurn`
  (`ta_in.llm.turn`: `content` = the assistant `text` / `tool_use` blocks as plain dicts, `stop_reason`, `.text`,
  `.tool_uses`); `complete()` is unchanged and returns `turn.text`. Retry, budget stop, cost records, record/replay and
  tracing share one path. `CacheEntry` gained optional `content` and `stop_reason` (key and `CACHE_VERSION` unchanged; an
  old text-only entry replays as one text block with `end_turn`), and the trace now fills `tool_calls`. **Loop:** new
  package `ta_in.agents`: `Tool(name, description, input_model, fn)` (Pydantic input model gives the JSON schema),
  `run_agent(client, *, agent, model, system, prompt, tools, max_steps=10, max_tokens)` -> `AgentResult(text, messages,
  steps, stopped_by, tool_calls)`. It calls `complete_turn`, runs every `tool_use` block of a turn and answers them in ONE
  user message of `tool_result` blocks, and stops on any stop reason other than `tool_use`. An unknown tool, input that
  fails the Pydantic model, or an exception in a tool comes back as `{"error": {"type", "message"}}` with
  `is_error: true`, so the model can recover. `PointInTimeError` and `BudgetExceeded` are deliberately NOT turned into tool
  results; they propagate. The step limit counts model calls and ends with `stopped_by="step_limit"` and no exception.
  The loop does not touch `AgentState`; agents (S2.3+) call it and write their own field. Tests: `tests/agents/test_loop.py`
  (scripted fake model, no SDK) and `tests/llm/test_tool_turns.py` (real `LLMClient` + fake SDK: parsing, record and replay,
  trace, and a whole agent run recorded then replayed with zero SDK calls). No real API call was made (no key).
- Previous story: S2.1 - typed `AgentState` (merged, PR #21; CI green, `passes` now true). New package `ta_in.state`: `AgentState` (frozen Pydantic: `run_id`, `ticker`, tz-aware `as_of`,
  `identity`, `price_snapshot`, `market_report` / `news_report` / `fundamentals_report`, `investment_debate`, `plan`,
  `proposal`, `risk_debate`, `decision`; types come from `ta_in.schemas` and `ta_in.data`, none redefined),
  `AnalystReport` (`status` ok | gap with a required `gap_reason`, `insufficient_data` flag, ready for S2.6 / S2.7),
  `DebateTurn`, `AgentName`, `OWNERSHIP` (the one table of who may write what) and `StateWriteError`. Writes only go
  through `state.update(agent, replace=False, **fields)` and `state.add_turn(agent, turn)`, which return a NEW state and
  refuse: a field the agent does not own, an unknown field, overwriting a field that is already set (set-once; pass
  `replace=True` to overwrite deliberately), a debate turn spoken by someone else, or a wrongly typed value. Round
  trip via `to_json()` / `from_json()` is lossless, tested with a fully populated state that uses the real frozen
  RELIANCE identity and `PriceSnapshot`. 47 tests in `tests/state/test_agent_state.py`, no network, no pandas.
  News and fundamentals data are deliberately not in the state (agents fetch them through tools; the state keeps the
  reports and the snapshot). The S1.7 loose end ("agents receive the snapshot") is now covered up to the state; S2.2 /
  S2.3 must still put `render_snapshot(...)` into the prompt context.
- **Decision 2026-11-11: S0.7 (baseline) and S7.3 (parity report) stay in `feature_list.json` but are deferred; we
  build the system without them.** Stories behind them stay blocked: S2.8 (needs S0.7), S7.3 itself, and S7.5 (needs
  S7.3). Nothing in S2.1-S4.5, S7.1, S7.2, S7.4 or Epic 6 depends on them. Revisit when the rest is built.
- Previous story: S1.8 - fixture set file and `freeze-fixtures` command (merged, PR #19; CI green, `passes` now true).
  **Epic 1 (data layer) is complete.**
- S1.8 real freeze: run 2026-11-11 17:08 IST (system Python, because Smart App Control blocks pandas in `.venv`).
  20 cells for 5 tickers, no failures; 272 bars per ticker (2025-10-07 to 2026-11-11), ~490 KB of snapshots.
  Past cells are `Withheld`, current cells have bfinance statements. News per cell ranges from 1 to 20 items; **20 is
  Currents' page size, so those cells (RELIANCE 11-04, INFY 10-28, HDFCBANK 10-21, ICICIBANK 10-21) are probably
  truncated**, and a thin cell (HDFCBANK 11-04 has 1 item) is simply what Currents returned. No news events were logged
  (nothing dropped, no duplicates, no provider failure). A spot check of INFY 10-28 showed relevant Infosys results
  coverage. Frozen as-is: bfinance unit quirks, no Nifty 50 prices.
- S1.8 summary: new package `ta_in.fixtures`. `FixtureSet` (`tests/fixtures/fixture_set.json`: RELIANCE, TCS, INFY,
  HDFCBANK, ICICIBANK; past dates 2026-10-21, 2026-10-28, 2026-11-04 at 16:00 IST; plus one "current" cell per ticker at
  the freeze moment; `holding_period_trading_days: 5` for this MVP set, because 20 does not fit Currents' 30-day
  limit; feature_list's pending default is untouched). `freeze_fixtures(...)` takes the EXISTING providers
  (PriceProvider, IdentityProvider, a NewsService factory, FundamentalsService) and writes under `tests/fixtures/`:
  `manifest.json`, `snapshots/prices/<T>.json` (all bars incl. after the last cell, for scoring),
  `snapshots/identity/<T>.json`, `snapshots/cells/<T>/<date>.json` (PriceSnapshot, news + NewsService events,
  fundamentals-or-Withheld, errors). It refuses to overwrite without `--force` and records a failing source as a
  failure in the manifest (CLI exits 1), never hides it. `FrozenDataProvider(root)` implements `DataProvider` from those
  files (filters then guards by `as_of`; `FixtureMissingError` if something was never frozen; `outcome_prices` for
  scoring). Indicators are NOT stored: they are derived from the frozen prices by `compute_indicator`. `effective_end`
  in `data/prices.py` is now public (was `_effective_end`). Tests: `tests/fixtures/test_freeze_offline.py` (fakes) and
  `tests/fixtures/test_frozen_fixtures.py` (both criteria, run against a generated snapshot dir always and against the
  committed real snapshots (23 tests pass locally now that `manifest.json` exists).
- To run the freeze again (needs Kite token, `CURRENTS_API_KEY`, and a Python where pandas loads): `freeze-fixtures`
  (`ta_in.fixtures.cli`; `--force` to replace). Re-freezing past cells later would lose news older than Currents'
  30-day limit, so treat the committed snapshots as the source of truth. Nifty 50 benchmark prices are not frozen
  (S7.2 must handle that).
- Previous story: S1.5 - fundamentals for current-date runs, `Withheld` for past dates (merged, PR #17; CI green,
  `passes` now true)
- Summary: new package `ta_in.fundamentals`, same shape as `ta_in.news`. `FundamentalsService(providers,
  max_age=2 days, clock=...).get_fundamentals(ticker, *, as_of)` returns `FundamentalsData` (ticker, as_of,
  `fetched_at`, and the income statement, balance sheet and cash flow as `{"YYYY-MM-DD": {item: float | None}}`) or a
  frozen `Withheld(ticker, as_of, reason)`. A call is current-date when `as_of >= clock() - max_age`; anything older
  is `Withheld` and **no provider is called**, so statements can never reach a backtest. If every provider fails or
  returns nothing the result is `Withheld` listing each failure, never an exception. The clock is injected; the data
  layer never reads "now" itself. Adapter: `BFinanceFundamentalsProvider` (bfinance 0.2.0, yearly statements via
  `Ticker.get_income_stmt / get_balance_sheet / get_cash_flow(as_dict=True)`; bfinance accepts `.NS` itself). It
  imports no pandas: NaN, infinity and non-numeric values become `None`, dates become strings, empty results raise
  `FundamentalsUnavailableError`. It ignores `as_of` (bfinance cannot serve a past date). Tests:
  `tests/data/test_fundamentals.py` (service, both criteria) and `tests/fundamentals/test_bfinance_adapter.py`; one
  `live` smoke test (not run). `DataProvider.fundamentals` now returns `FundamentalsData | Withheld`. This branch
  also carries the S1.4 `passes=true` flip and this file's S1.4 notes (folded in by decision).
- Previous story: S1.4 - dated news up to as_of, deduplicated, as typed NewsItem (merged, PR #16; CI green)
- S1.4 summary: new package `ta_in.news` (kept apart from `ta_in/data`, depends only on `ta_in.data.point_in_time`):
  `NewsItem` (frozen; title, source, tz-aware `published_time` stored in UTC, url, `summary | None`),
  `NewsProvider` Protocol (one adapter per source; must raise on failure), `NewsService(providers,
  event_sink=None).get_news(ticker, *, as_of)` and `canonical_url`. The service collects from every provider (a
  failing one is a `news.provider_failed` gap, never a crash), drops items published after `as_of` (compared as
  instants, each drop logged as `news.dropped_after_as_of`), de-duplicates (canonical URL with tracking params
  stripped, else title+time; the earliest copy wins), sorts oldest first, runs survivors through `guard_records`,
  and returns `[]` (logged as `news.empty`) when there is nothing. Adapter: `CurrentsNewsProvider` (Currents API
  only; NSE/jugaad-data was dropped by decision). It raises on HTTP errors and keeps the response body in the
  message. New setting `CURRENTS_API_KEY` (unprefixed, optional `SecretStr`). Tests: `tests/data/test_news.py`
  (service, all three criteria) and `tests/news/test_news_adapters.py`. `scripts/news_client.py` is a manual client
  (key from the environment or `.env`; `--as-of`, `--name`, `--lookback-days`) that also prints the `NewsItem` shape.
  Checked by hand against Currents on 2026-11-11 (current dates work). **Currents limits (see Open issues):** 30
  days back from today, 7 days per request, and the query needs the company name (`INFY` alone gave 0 items).
  The company-name lookup is not wired to `IdentityProvider` yet (S2.4 / S4.5). `DataProvider.news` now returns
  `Sequence[NewsItem]`; `.claude/rules/data.md` now also covers `news/` and `fundamentals/`.
- Local note: Windows Smart App Control blocks mypy and pandas here, so `verify` and the commit hooks cannot run
  locally; S1.4 was committed with `--no-verify` (approved) and verified in CI instead.
- Previous story: S1.7 - verified price snapshot for grounding
- S1.7 summary: `ta_in.data.snapshot` with `PriceSnapshot` (frozen: `as_of_date`, `last_close`, `high_52w` /
  `low_52w` with their dates, `history_complete_52w`, `return_1d/5d/20d/60d`), `build_snapshot(bars, *, as_of)`
  and `render_snapshot(snapshot, ticker)`. Everything comes from `close` (not `adj_close`). The 52-week
  window is `date >= last_date - 365 days` (inclusive); the earliest date wins a tie; `history_complete_52w`
  is false when the first bar is inside the window, and the rendered text then says the range is partial.
  Returns are over 1, 5, 20 and 60 trading bars and are `None` when there are too few bars. Bars go through
  `guard_records` (a later bar raises `PointInTimeError`) and must be strictly ascending. 12 offline tests in
  `tests/data/test_snapshot.py`, including an exact golden text and a run through `PriceProvider`.
  **Criterion 2 ("agents receive the snapshot in their context") is only covered up to the render function.**
  Ticked on purpose to avoid a deadlock (S2.1 and S2.2 depend on S1.7). The real injection must be verified in
  later stories: S2.1's `AgentState` should hold a `PriceSnapshot`, S2.2/S2.3 should put `render_snapshot(...)` in
  the agent context, and S2.3 compares report figures to the snapshot.
- Previous story: S1.3 - indicators from prices (RSI, MACD, Bollinger, ATR, SMA, EMA, VWMA)
- S1.3 summary: `ta_in.data.indicators` with `compute_indicator(bars, kind, *, as_of, window=None)`,
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
- S1.3: indicators via stockstats (merged, PR #14)
- S1.7: verified price snapshot (merged, PR #15; criterion 2 covered up to render only)
- S1.4: dated news from Currents (merged, PR #16)
- S1.5: fundamentals via bfinance, `Withheld` for past dates (merged, PR #17)
- S1.8: fixtures package, `freeze-fixtures` and the frozen snapshots (merged, PR #19, passes flipped in #20)
- S2.1: typed `AgentState` with per-agent field ownership (merged, PR #21)

## Next

- After S2.2: S2.3 (market analyst) and on through Epic 2. S0.7 / S7.3 are deferred (see Last session). S0.7 would need the upstream v0.7.0 code cloned into
  its own environment, an Anthropic key, a budget and a data-source decision (own vendors vs our frozen snapshots).
- Post-MVP: add runtime caching for news and fundamentals (decided 2026-11-11; not a listed story). Today only prices,
  identity and LLM responses are cached; each news call is a Currents request and each fundamentals call goes to
  bfinance (which has its own 24-hour cache we do not control). Idea: a per-ticker on-disk news cache that records
  each item with its publication time and the range covered (fetch only the missing part, like the price cache), which
  also builds an archive past Currents' 30-day limit; fundamentals cached with `fetched_at`, served only for
  current-date runs. It must never return anything dated after `as_of`, with an offline test that proves it.
- Past fixture news is now frozen for the MVP set; news for any NEW past date still cannot be fetched (Currents
  reaches back 30 days only); see Open issues.
- Agent tests should load data through `FrozenDataProvider` (`ta_in.fixtures`), never the live providers.

## Open issues

- The S1.2 yfinance live test (`test_live_yfinance_smoke`) failed on 2026-11-06 with a connect timeout to Yahoo
  from the dev machine and has never passed. Run `uv run pytest -m live tests/data/test_prices.py` where Yahoo
  is reachable.
- The Kite live test (`test_live_kite_smoke`) is skipped on purpose: it needs `TA_KITE_API_KEY` and
  `TA_KITE_ACCESS_TOKEN` exported in the shell, and the owner does not want secrets passed to test runs. It
  ignores `.env` (`Settings(_env_file=None)`). Kite was only checked by hand (see Last session).
- Kite access tokens expire daily and history needs the paid Historical API add-on.
- **Currents news only reaches back 30 days from the moment of the request** (found by hand on 2026-11-11:
  HTTP 400 `date_range: Max lookback is 30 days` for as_of 2025-06-30; today's date worked). The adapter now
  raises a clear error for an `as_of` older than that (a logged gap, no request sent) and clamps a window that
  only partly reaches back. Consequence: news for past fixture dates cannot be fetched on this plan. Options: pick
  fixture dates inside the window and freeze them (S1.8) before they age out, upgrade the plan, or add another
  dated source. Not decided. A 7-day window also returned only 3 items, some loosely related; query terms are untuned.
- bfinance (S1.5) was run against the real service on 2026-11-11 with `scripts/fundamentals_client.py RELIANCE.NS`
  (using the system Python, because Smart App Control blocks pandas inside this repo's `.venv`): it returned
  `FundamentalsData` with 12 income-statement periods (newest 2026-03-31, 15 items), 13 balance-sheet periods (newest
  **2026-09-30**, a half-year, 10 items) and 12 cash-flow periods (6 items). The `live` pytest has not been run, and
  figures were not compared with filings. Quirks seen: periods differ between statements (balance sheet newer than
  income); values look converted from rupees crore to rupees, and a growth figure came out as `Sales growth:
  100000000.0`, so non-currency items (ratios, growth) look scaled too and must not be trusted or quoted without
  checking. Yearly statements only (quarterly raises in bfinance). Past-dated fundamentals are withheld by design,
  so past fixture cells have no fundamentals; the fundamentals analyst (S2.5) must say so.
- **Currents allows at most 7 days per request** (`date_range: Max range is 7 days`, found by hand on 2026-11-11);
  `CurrentsNewsProvider` rejects `lookback_days` over 7. Longer history would need several requests, which the
  30-day lookback limit above also caps.
- **Currents query needs the company name, not only the symbol.** `INFY.NS` searched as `"INFY"` gave 0 items;
  `INFOSYS` gave 4 (by hand, 2026-11-11). The adapter now builds `("Infosys" OR "INFY") AND (finance terms)` when
  given a `name_resolver` (legal suffixes like Limited are dropped). Nothing wires the real resolver yet: it should
  be `IdentityProvider` / `CompanyIdentity.name` (S1.6), to be connected when the news analyst (S2.4) or `propagate`
  (S4.5) builds the provider. `scripts/news_client.py --name "Infosys Limited"` passes a name by hand.
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
- Cache entries now store the content blocks and stop reason (S2.2), so tool-use responses are cached; `cost` of
  cache-read/creation tokens is still not modelled. The trace fills `tool_calls` (S2.2); trace time is summed call latency,
  not wall-clock.
- Not ported from upstream yet: `SentimentBand`/`SentimentReport`, `render_*` helpers, `extract_rating`/`parse_rating` (in local `docs/`).
- Upstream licence not recorded in the source comment; confirm the licence text before redistributing.
