"""`DataProvider` backed only by frozen snapshot files. It never touches the network."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ta_in.data.identity import CompanyIdentity
from ta_in.data.point_in_time import guard_records, is_visible
from ta_in.data.price_cache import PriceBar
from ta_in.data.prices import effective_end
from ta_in.data.snapshot import PriceSnapshot
from ta_in.fixtures.models import (
    CellSnapshot,
    FixtureMissingError,
    PricesFile,
    cell_path,
    identity_path,
    prices_path,
)
from ta_in.fundamentals.models import FundamentalsData, Withheld
from ta_in.news.models import NewsItem

M = TypeVar("M", bound=BaseModel)


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")


class FrozenDataProvider:
    """Implements `DataProvider` from the files `freeze_fixtures` wrote.

    Like the live providers it returns nothing dated after `as_of`: the prices file holds later
    bars (for scoring), so they are filtered here and then checked by `guard_records`. A request
    for something that was never frozen raises `FixtureMissingError`.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _read(self, path: Path, model: type[M]) -> M:
        if not path.exists():
            raise FixtureMissingError(f"nothing frozen at {path}")
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise FixtureMissingError(
                f"unreadable snapshot {path}: {exc.error_count()} errors"
            ) from exc

    def _cell(self, ticker: str, as_of: datetime) -> CellSnapshot:
        _require_aware(as_of)
        return self._read(cell_path(self._root, ticker.strip().upper(), as_of), CellSnapshot)

    def outcome_prices(self, ticker: str) -> Sequence[PriceBar]:
        """Every frozen bar, including those after the cells' dates. For scoring only."""
        return self._read(prices_path(self._root, ticker.strip().upper()), PricesFile).bars

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        _require_aware(as_of)
        last = effective_end(as_of)
        visible = [b for b in self.outcome_prices(ticker) if b.date <= last]
        return guard_records(visible, as_of, lambda b: b.date)

    def price_snapshot(self, ticker: str, *, as_of: datetime) -> PriceSnapshot | None:
        return self._cell(ticker, as_of).price_snapshot

    def news(self, ticker: str, *, as_of: datetime) -> Sequence[NewsItem]:
        items = [i for i in self._cell(ticker, as_of).news if is_visible(i.published_time, as_of)]
        return guard_records(items, as_of, lambda i: i.published_time)

    def fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld:
        found = self._cell(ticker, as_of).fundamentals
        if found is None:
            raise FixtureMissingError(f"fundamentals were not frozen for {ticker} at {as_of}")
        return found

    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity:
        _require_aware(as_of)
        return self._read(identity_path(self._root, ticker.strip().upper()), CompanyIdentity)
