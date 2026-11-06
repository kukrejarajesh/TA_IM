"""Company identity (name, sector, exchange) for a `.NS` ticker, resolved once and cached.

Identity is treated as static: `as_of` is validated but never changes the answer. Renames and
sector reclassifications over time are not modelled.
"""

from __future__ import annotations

import csv
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)

SECTOR_MAP_PATH = Path(__file__).with_name("sector_map.csv")


class UnknownTickerError(ValueError):
    """No company matches the ticker. Raised before any agent runs."""


class CompanyIdentity(BaseModel):
    """Who the company is. Carries no timestamp, so equal inputs give equal values."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    name: str
    sector: str | None
    exchange: str


# Takes a normalised ticker such as `RELIANCE.NS`; raises `UnknownTickerError` if not found.
Resolver = Callable[[str], CompanyIdentity]


def load_sector_map(path: Path = SECTOR_MAP_PATH) -> dict[str, str]:
    """Read a `symbol,sector` CSV into {SYMBOL: sector}."""
    if not path.exists():
        raise ValueError(f"sector map not found: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or [n.strip() for n in reader.fieldnames] != [
            "symbol",
            "sector",
        ]:
            raise ValueError(f"sector map {path} must have the header: symbol,sector")
        out: dict[str, str] = {}
        for row in reader:
            symbol = row["symbol"].strip().upper()
            if symbol in out:
                raise ValueError(f"duplicate symbol {symbol!r} in sector map {path}")
            out[symbol] = row["sector"].strip()
    return out


class IdentityProvider:
    """`DataProvider.identity` for NSE tickers, backed by a resolver and an on-disk cache."""

    def __init__(self, resolver: Resolver, cache_dir: Path) -> None:
        self._resolver = resolver
        self._root = cache_dir / "identity"

    def _path(self, ticker: str) -> Path:
        return self._root / f"{ticker}.json"

    def _load(self, ticker: str) -> CompanyIdentity | None:
        path = self._path(ticker)
        if not path.exists():
            return None
        try:
            return CompanyIdentity.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError):
            log.warning("ignoring unreadable identity cache for %s", ticker)
            return None

    def _save(self, identity: CompanyIdentity) -> None:
        path = self._path(identity.ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(identity.model_dump_json(), encoding="utf-8")
        os.replace(tmp, path)

    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        ticker = ticker.strip().upper()
        if not ticker.endswith(".NS"):
            raise ValueError(f"only NSE tickers ending in .NS are supported, got {ticker!r}")

        cached = self._load(ticker)
        if cached is not None:
            return cached
        found = self._resolver(ticker)  # failures propagate and are never cached
        self._save(found)
        return found
