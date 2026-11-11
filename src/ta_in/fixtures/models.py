"""Fixture set (the input) and the frozen snapshot records (the output), plus their file paths."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ta_in.data.identity import CompanyIdentity
from ta_in.data.point_in_time import IST
from ta_in.data.price_cache import PriceBar
from ta_in.data.snapshot import PriceSnapshot
from ta_in.fundamentals.models import FundamentalsData, Withheld
from ta_in.news.models import NewsItem


class FixtureExistsError(Exception):
    """A snapshot already exists and `force` was not given."""


class FixtureMissingError(Exception):
    """A frozen snapshot was asked for that was never frozen. Nothing is fetched instead."""


class FixtureCell(BaseModel):
    """One ticker on one decision moment."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: datetime


class FixtureSet(BaseModel):
    """What to freeze: tickers x past decision dates, plus one 'current' cell on the freeze day."""

    model_config = ConfigDict(frozen=True)

    tickers: list[str]
    past_dates: list[date]
    decision_time: time = time(16, 0)  # IST, after the 15:30 close
    include_current: bool = True
    holding_period_trading_days: int = Field(default=5, ge=1)

    @field_validator("tickers")
    @classmethod
    def _nse_unique(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip().upper() for t in v]
        if not cleaned:
            raise ValueError("tickers must not be empty")
        if any(not t.endswith(".NS") for t in cleaned):
            raise ValueError("every ticker must end in .NS")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("tickers must be unique")
        return cleaned

    def past_cells(self) -> list[FixtureCell]:
        return [
            FixtureCell(ticker=t, as_of=datetime.combine(d, self.decision_time, tzinfo=IST))
            for t in self.tickers
            for d in sorted(self.past_dates)
        ]

    def current_cells(self, now: datetime) -> list[FixtureCell]:
        if not self.include_current:
            return []
        return [FixtureCell(ticker=t, as_of=now.astimezone(IST)) for t in self.tickers]

    def cells(self, now: datetime) -> list[FixtureCell]:
        return self.past_cells() + self.current_cells(now)


def load_fixture_set(path: Path) -> FixtureSet:
    return FixtureSet.model_validate_json(path.read_text(encoding="utf-8"))


class PricesFile(BaseModel):
    """All daily bars frozen for a ticker, including bars after the last cell (for scoring)."""

    ticker: str
    frozen_at: datetime
    source: str
    bars: list[PriceBar]


class CellSnapshot(BaseModel):
    """Everything frozen for one cell that depends on its `as_of`."""

    ticker: str
    as_of: datetime
    frozen_at: datetime
    price_snapshot: PriceSnapshot | None
    news: list[NewsItem]
    news_events: list[dict[str, Any]]  # drops, duplicates and gaps logged by NewsService
    fundamentals: FundamentalsData | Withheld | None
    errors: list[str]


class ManifestCell(BaseModel):
    ticker: str
    as_of: datetime
    news_count: int
    fundamentals: Literal["data", "withheld", "error"]
    errors: list[str]


class ManifestTicker(BaseModel):
    ticker: str
    bars: int
    first_bar: date
    last_bar: date


class Manifest(BaseModel):
    """Index of what a freeze wrote. `failures` is empty only when everything succeeded."""

    frozen_at: datetime
    holding_period_trading_days: int
    sources: dict[str, str]
    tickers: list[ManifestTicker]
    cells: list[ManifestCell]
    failures: list[str]


def _day(as_of: datetime) -> str:
    return as_of.astimezone(IST).date().isoformat()


def prices_path(root: Path, ticker: str) -> Path:
    return root / "snapshots" / "prices" / f"{ticker}.json"


def identity_path(root: Path, ticker: str) -> Path:
    return root / "snapshots" / "identity" / f"{ticker}.json"


def cell_path(root: Path, ticker: str, as_of: datetime) -> Path:
    return root / "snapshots" / "cells" / ticker / f"{_day(as_of)}.json"


__all__ = [
    "CellSnapshot",
    "CompanyIdentity",
    "FixtureCell",
    "FixtureExistsError",
    "FixtureMissingError",
    "FixtureSet",
    "Manifest",
    "ManifestCell",
    "ManifestTicker",
    "PricesFile",
    "cell_path",
    "identity_path",
    "load_fixture_set",
    "prices_path",
]
