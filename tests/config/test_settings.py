from pathlib import Path

import pytest
from pydantic import ValidationError

from ta_in.config import Settings, SettingsError, load_settings

FAKE_KEY = "sk-ant-FAKE-0123456789-secret"

ENV_NAMES = [
    "ANTHROPIC_API_KEY",
    "TA_PROVIDER",
    "TA_QUICK_MODEL",
    "TA_DEEP_MODEL",
    "TA_DEBATE_ROUNDS",
    "TA_RISK_ROUNDS",
    "TA_TEMPERATURE",
    "TA_BUDGET_USD",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Run from an empty dir so a real .env in the repo root is never picked up.
    monkeypatch.chdir(tmp_path)


def test_defaults_when_only_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    s = load_settings()
    assert s.provider == "anthropic"
    assert s.quick_model == "claude-haiku-5-5"
    assert s.deep_model == "claude-sonnet-5-5"
    assert s.debate_rounds == 1
    assert s.risk_rounds == 1
    assert s.temperature == 0.0
    assert s.budget_usd == 1.0
    assert s.anthropic_api_key.get_secret_value() == FAKE_KEY


def test_all_fields_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TA_QUICK_MODEL", "quick-x")
    monkeypatch.setenv("TA_DEEP_MODEL", "deep-y")
    monkeypatch.setenv("TA_DEBATE_ROUNDS", "3")
    monkeypatch.setenv("TA_RISK_ROUNDS", "2")
    monkeypatch.setenv("TA_TEMPERATURE", "0.7")
    monkeypatch.setenv("TA_BUDGET_USD", "12.5")
    s = load_settings()
    assert (s.quick_model, s.deep_model) == ("quick-x", "deep-y")
    assert (s.debate_rounds, s.risk_rounds) == (3, 2)
    assert s.temperature == 0.7
    assert s.budget_usd == 12.5


def test_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\nTA_DEBATE_ROUNDS=4\n", encoding="utf-8")
    s = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert s.debate_rounds == 4


def test_missing_key_fails_with_clear_message() -> None:
    with pytest.raises(SettingsError) as exc:
        load_settings()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_empty_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(SettingsError) as exc:
        load_settings()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("TA_DEBATE_ROUNDS", "0", "debate_rounds"),
        ("TA_RISK_ROUNDS", "0", "risk_rounds"),
        ("TA_TEMPERATURE", "1.5", "temperature"),
        ("TA_TEMPERATURE", "-0.1", "temperature"),
        ("TA_BUDGET_USD", "0", "budget_usd"),
        ("TA_BUDGET_USD", "-1", "budget_usd"),
        ("TA_PROVIDER", "openai", "provider"),
        ("TA_DEBATE_ROUNDS", "many", "debate_rounds"),
    ],
)
def test_invalid_values_fail_naming_the_field(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, field: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError) as exc:
        load_settings()
    assert field in str(exc.value)


def test_all_problems_reported_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TA_DEBATE_ROUNDS", "0")
    monkeypatch.setenv("TA_BUDGET_USD", "-1")
    with pytest.raises(SettingsError) as exc:
        load_settings()
    msg = str(exc.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "debate_rounds" in msg
    assert "budget_usd" in msg


def test_key_never_appears_in_representations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    s = load_settings()
    for text in (repr(s), str(s), str(s.model_dump()), s.model_dump_json()):
        assert FAKE_KEY not in text


def test_key_never_appears_in_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TA_DEBATE_ROUNDS", "0")
    with pytest.raises(SettingsError) as exc:
        load_settings()
    assert FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in repr(exc.value)


def test_settings_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    s = load_settings()
    with pytest.raises(ValidationError):
        s.debate_rounds = 5


def test_cache_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    s = load_settings()
    assert s.cache_mode == "off"
    assert s.cache_dir == Path(".llm_cache")
    monkeypatch.setenv("TA_CACHE_MODE", "replay")
    monkeypatch.setenv("TA_CACHE_DIR", "fixtures/llm")
    s = load_settings()
    assert s.cache_mode == "replay"
    assert s.cache_dir == Path("fixtures/llm")


def test_invalid_cache_mode_fails_naming_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TA_CACHE_MODE", "sometimes")
    with pytest.raises(SettingsError) as exc:
        load_settings()
    assert "cache_mode" in str(exc.value)
