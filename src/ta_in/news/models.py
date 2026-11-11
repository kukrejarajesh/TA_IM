"""Typed news record shared by every news adapter and the analyst layer."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NewsItem(BaseModel):
    """One dated news item. `published_time` is always timezone-aware and stored in UTC."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    published_time: datetime
    url: str
    summary: str | None = None

    @field_validator("published_time")
    @classmethod
    def _require_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("published_time must be timezone-aware")
        return v.astimezone(UTC)
