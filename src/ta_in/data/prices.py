"""Daily OHLCV for .NS tickers: fetched via an injected fetcher, cached, point-in-time safe."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from ta_in.data.point_in_time import IST, guard_records
from ta_in.data.price_cache import CachedPrices, PriceBar, PriceCache

log = logging.getLogger(__name__)

# NSE closes at 15:30 IST; a bar is only treated as published from then on.
MARKET_CLOSE = time(15, 30)

# Fetches bars for the inclusive range [start, end]. May over-return; the provider filters.
Fetcher = Callable[[str, date, date], list[PriceBar]]


def _effective_end(as_of: datetime) -> date:
    """Last trading date whose bar was published at `as_of`."""
    local = as_of.astimezone(IST)
    return local.date() if local.time() >= MARKET_CLOSE else local.date() - timedelta(days=1)


def _merge(old: list[PriceBar], new: list[PriceBar]) -> list[PriceBar]:
    by_date = {b.date: b for b in old}
    by_date.update({b.date: b for b in new})
    return [by_date[d] for d in sorted(by_date)]


class PriceProvider:
    """`DataProvider.prices` for NSE tickers. Holidays are simply absent; none are invented."""

    def __init__(self, fetcher: Fetcher, cache_dir: Path, *, lookback_days: int = 400) -> None:
        self._fetcher = fetcher
        self._cache = PriceCache(cache_dir)
        self._lookback = timedelta(days=lookback_days)

    def _fetch(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        raw = self._fetcher(ticker, start, end)
        kept = [b for b in raw if start <= b.date <= end]
        if len(kept) != len(raw):
            log.warning(
                "%s: dropped %d bars outside %s..%s", ticker, len(raw) - len(kept), start, end
            )
        return kept

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not ticker.endswith(".NS"):
            raise ValueError(f"only NSE tickers ending in .NS are supported, got {ticker!r}")

        end = _effective_end(as_of)
        start = end - self._lookback
        entry = self._cache.load(ticker)
        if entry is None:
            entry = CachedPrices(start=start, through=end, bars=self._fetch(ticker, start, end))
            self._cache.save(ticker, entry)
        else:
            changed = False
            if start < entry.start:
                head = self._fetch(ticker, start, entry.start - timedelta(days=1))
                entry = CachedPrices(
                    start=start, through=entry.through, bars=_merge(entry.bars, head)
                )
                changed = True
            if end > entry.through:
                tail = self._fetch(ticker, entry.through + timedelta(days=1), end)
                entry = CachedPrices(start=entry.start, through=end, bars=_merge(entry.bars, tail))
                changed = True
            if changed:
                self._cache.save(ticker, entry)

        # Filter on every read: the cache may hold bars later than this caller's as_of.
        visible = [b for b in entry.bars if start <= b.date <= end]
        return guard_records(visible, as_of, lambda b: b.date)


def yfinance_fetcher(
    ticker: str,
    start: date,
    end: date,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
) -> list[PriceBar]:
    """Fetch daily bars from yfinance for [start, end]. Drops NaN and zero-volume rows."""
    if ticker_factory is None:
        import yfinance

        ticker_factory = yfinance.Ticker
    # yfinance treats `end` as exclusive.
    frame = ticker_factory(ticker).history(
        start=start, end=end + timedelta(days=1), auto_adjust=False
    )
    bars: list[PriceBar] = []
    for ts, row in frame.iterrows():
        values = [row["Open"], row["High"], row["Low"], row["Close"], row["Adj Close"]]
        volume = row["Volume"]
        if any(v != v for v in values) or volume != volume or int(volume) == 0:
            continue
        stamp = ts.to_pydatetime()
        day = stamp.astimezone(IST).date() if stamp.tzinfo else stamp.date()
        bars.append(
            PriceBar(
                date=day,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                adj_close=float(row["Adj Close"]),
                volume=int(volume),
            )
        )
    return bars
