# Progress

## Last session

- Date: 2026-10-31
- Story: S0.8 - shared decision models
- Summary: `ta_in.schemas` with `PortfolioRating`, `TraderAction`, `ResearchPlan`, `TraderProposal`,
  `PortfolioDecision`, `RATINGS_5_TIER`, `RATING_REVIEW`, copied from TradingAgents v0.7.0 (source files noted
  in `decision.py`). Names, labels, descriptions and defaults checked identical to upstream. One deviation:
  the rupee sign is stripped from price strings. 72 tests in `tests/schemas/test_decision.py`. `docs/`
  (upstream source) is gitignored.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (merged to main, PR #2; default budget $1)
- S0.3: LLM client wrapper with retry, cost records and budget stop (merged, PR #3)
- S0.4: response cache with record and replay (merged, PR #4)
- S0.5: run trace and trace-summary (merged, PR #5)
- S0.8: shared decision models (branch `s0.8-decision-types`, PR pending)

## Next

- S1.1: DataProvider interface + PointInTimeError guard (next in list order).
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
