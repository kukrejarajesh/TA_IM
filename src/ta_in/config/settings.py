"""Runtime settings loaded from the environment / .env, validated at start-up."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(Exception):
    """Raised when settings are missing or invalid. Never contains input values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TA_",
        extra="ignore",
        frozen=True,
    )

    anthropic_api_key: SecretStr = Field(min_length=1, validation_alias="ANTHROPIC_API_KEY")
    provider: Literal["anthropic"] = "anthropic"
    quick_model: str = "claude-haiku-5-5"
    deep_model: str = "claude-sonnet-5-5"
    debate_rounds: int = Field(default=1, ge=1)
    risk_rounds: int = Field(default=1, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    budget_usd: float = Field(default=1.0, gt=0.0)
    cache_mode: Literal["off", "record", "replay"] = "off"
    cache_dir: Path = Path(".llm_cache")
    trace_dir: Path = Path("traces")
    data_cache_dir: Path = Path(".data_cache")


def _env_name(field: str) -> str:
    return "ANTHROPIC_API_KEY" if field == "anthropic_api_key" else field


def load_settings() -> Settings:
    """Load settings, or raise SettingsError listing every problem (field and reason only)."""
    try:
        return Settings()
    except ValidationError as err:
        lines = [
            f"  - {_env_name(str(e['loc'][-1]))}: {e['msg']}" if e["loc"] else f"  - {e['msg']}"
            for e in err.errors()
        ]
        raise SettingsError("Invalid settings:\n" + "\n".join(lines)) from None
