# Progress

## Last session

- Date: 2026-10-31
- Story: S0.2 - settings from .env with Pydantic
- Summary: `ta_in.config` with `Settings` (pydantic-settings, `SecretStr` key, `TA_` prefix) and
  `load_settings()`, which raises `SettingsError` listing each bad field without echoing values.
  17 offline tests in `tests/config/test_settings.py`. `scripts/verify.py` passes locally.

## Done

- S0.1: repo skeleton with harness
- Feature list schema tests and `passes` guard hook
- S0.2: settings from .env with Pydantic (on branch `s0.2-settings`, PR pending)

## Next

- S0.3: LLM client wrapper over the Anthropic Messages API (retry, backoff, cost logging, budget stop).

## Open issues

- The guard is a local pre-commit hook only; `git commit --no-verify` skips it. No CI check by choice.
- `.python-version` pins 3.13 (uv's managed 3.12 download failed in this sandbox); CI covers 3.11/3.12.
- Broken uv junction at `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (harmless warning).
- Default model IDs in `Settings` are provisional.
