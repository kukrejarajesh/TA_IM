"""On-disk cache of daily bars, one JSON file per ticker."""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)


class PriceBar(BaseModel):
    """One daily OHLCV bar, keyed by its IST trading date."""

    model_config = ConfigDict(frozen=True)

    date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int


class CachedPrices(BaseModel):
    """Bars fetched for the inclusive date range [start, through]."""

    start: date
    through: date
    bars: list[PriceBar]


class PriceCache:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, ticker: str) -> Path:
        return self._root / "prices" / f"{ticker}.json"

    def load(self, ticker: str) -> CachedPrices | None:
        path = self._path(ticker)
        if not path.exists():
            return None
        try:
            return CachedPrices.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError):
            log.warning("ignoring unreadable price cache for %s", ticker)
            return None

    def save(self, ticker: str, entry: CachedPrices) -> None:
        path = self._path(ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(entry.model_dump_json(), encoding="utf-8")
        os.replace(tmp, path)
