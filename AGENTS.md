# TradingAgents-IN

Python rebuild of TradingAgents v0.7.0 for NSE stocks. Paper trading only.

## Commands

- Setup (first time, system Python): `python scripts/init.py` (`init.sh` / `init.ps1`)
- Verify (must pass before any commit): `uv run python scripts/verify.py` (`verify.sh` / `verify.ps1`)
  - runs in order, stops at first failure: `ruff format --check`, `ruff check`, `mypy src`, `pytest -m "not live"`
- Format: `uv run ruff format`
- Auto-fix lint: `uv run ruff check --fix`
- Single test: `uv run pytest tests/test_x.py::test_name`
- Live tests (network, opt-in only): `uv run pytest -m live`
- Run cost/time per agent: `uv run trace-summary <run-id-or-path>`

## Repo map

- `src/ta_in/` - the package (src layout)
- `src/ta_in/config/` - `Settings` / `load_settings()`; env + `.env`, secrets as `SecretStr`
- `src/ta_in/llm/` - `LLMClient` (retry/backoff, token+cost records, `BudgetExceeded`), `pricing.py`, `cache.py` (record/replay), `trace.py` (one JSONL per run)
- `src/ta_in/schemas/` - shared decision types (`PortfolioRating`, `PortfolioDecision`, ...); import from here, never redefine
- `src/ta_in/data/` - data access; point-in-time rules apply (see `.claude/rules/data.md`)
- `tests/` - offline pytest suite; `live` marker for network tests
- `scripts/` - `verify.py`, `init.py` and their shell wrappers
- `.claude/rules/` - path-scoped agent rules
- `PROGRESS.md` - session log: last session, done, next, open issues
- `feature_list.json` - story/feature checklist with `passes` flags (only `passes` may change; guarded by pre-commit)
- `.env.example` - env var names only; real values live in untracked `.env`

## Hard rules

1. Every data call takes `as_of` and never returns data later than `as_of`.
2. No broker or order-execution code. Paper trading only.
3. Never read or print `.env`. Use `.env.example` for variable names.
4. Tests run offline. Anything that hits the network is marked `live` and excluded by default.
5. Never weaken a test or acceptance criterion to make it pass. Fix the code, or ask.

## Workflow

1. Read `PROGRESS.md` and `feature_list.json`.
2. Pick one feature with `passes=false`.
3. Write the test first.
4. Implement, then run verify until it is green.
5. Only then set `passes=true` in `feature_list.json`.
6. Commit with the story ID in the message (e.g. `S0.1: ...`).
7. Update `PROGRESS.md`.
