"""S1.2: daily OHLCV for .NS tickers, point-in-time safe and cached locally."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ta_in.data import IST, PriceBar, PriceProvider, yfinance_fetcher

TICKER = "RELIANCE.NS"
HOLIDAY = date(2026, 1, 26)  # Republic Day, a weekday with no trading


def _bar(d: date, close: float = 100.0) -> PriceBar:
    return PriceBar(
        date=d,
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        adj_close=close,
        volume=1_000,
    )


def _trading_days(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d != HOLIDAY:
            out.append(d)
        d += timedelta(days=1)
    return out


class FakeFetcher:
    """Vendor stand-in. Like a careless vendor it ignores `end` and returns bars up to Feb 27."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self.bars = [
            _bar(d, 100.0 + i)
            for i, d in enumerate(_trading_days(date(2025, 12, 1), date(2026, 2, 27)))
        ]

    def __call__(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        self.calls.append((ticker, start, end))
        return [b for b in self.bars if b.date >= start]


def _provider(tmp_path: Path, fetcher: FakeFetcher, lookback_days: int = 30) -> PriceProvider:
    return PriceProvider(fetcher, tmp_path, lookback_days=lookback_days)


def _at(day: int, hour: int = 16, minute: int = 0, month: int = 2) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


def test_no_bar_after_as_of_is_returned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fetcher = FakeFetcher()
    as_of = _at(10)
    with caplog.at_level(logging.WARNING):
        bars = _provider(tmp_path, fetcher).prices(TICKER, as_of=as_of)
    assert bars
    assert max(b.date for b in bars) == date(2026, 2, 10)
    assert all(b.date <= as_of.date() for b in bars)
    assert "dropped" in caplog.text


def test_today_bar_hidden_until_market_close(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    provider = _provider(tmp_path, fetcher)
    before = provider.prices(TICKER, as_of=_at(10, 15, 29))
    after = provider.prices(TICKER, as_of=_at(10, 15, 30))
    assert before[-1].date == date(2026, 2, 9)
    assert after[-1].date == date(2026, 2, 10)


def test_holidays_and_weekends_are_not_fabricated(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    bars = _provider(tmp_path, fetcher).prices(TICKER, as_of=_at(3))
    dates = [b.date for b in bars]
    assert HOLIDAY not in dates
    assert all(d.weekday() < 5 for d in dates)
    assert dates == sorted(dates)
    assert dates == _trading_days(date(2026, 1, 4), date(2026, 2, 3))


def test_second_call_is_served_from_cache(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    provider = _provider(tmp_path, fetcher)
    first = provider.prices(TICKER, as_of=_at(10))
    second = provider.prices(TICKER, as_of=_at(10))
    assert len(fetcher.calls) == 1
    assert first == second


def test_cache_survives_a_new_provider_instance(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    first = _provider(tmp_path, fetcher).prices(TICKER, as_of=_at(10))
    second = _provider(tmp_path, fetcher).prices(TICKER, as_of=_at(10))
    assert len(fetcher.calls) == 1
    assert first == second


def test_later_as_of_fetches_only_the_tail(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    provider = _provider(tmp_path, fetcher)
    provider.prices(TICKER, as_of=_at(10))
    bars = provider.prices(TICKER, as_of=_at(12))
    assert len(fetcher.calls) == 2
    assert fetcher.calls[1] == (TICKER, date(2026, 2, 11), date(2026, 2, 12))
    assert bars[-1].date == date(2026, 2, 12)


def test_cache_holding_later_bars_never_leaks_them(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    provider = _provider(tmp_path, fetcher)
    provider.prices(TICKER, as_of=_at(27))  # cache now reaches Feb 27
    bars = provider.prices(TICKER, as_of=_at(20))
    assert max(b.date for b in bars) == date(2026, 2, 20)
    # only the older head was missing; nothing past the cached tail was requested
    assert all(call[2] <= date(2026, 2, 27) for call in fetcher.calls)
    assert len(fetcher.calls) == 2


def test_empty_result_is_an_explicit_empty_list(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    fetcher.bars = []
    assert _provider(tmp_path, fetcher).prices(TICKER, as_of=_at(10)) == []


def test_non_ns_ticker_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.NS"):
        _provider(tmp_path, FakeFetcher()).prices("AAPL", as_of=_at(10))


def test_naive_as_of_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _provider(tmp_path, FakeFetcher()).prices(TICKER, as_of=datetime(2026, 2, 10, 16, 0))


def test_as_of_is_keyword_only(tmp_path: Path) -> None:
    provider: Any = _provider(tmp_path, FakeFetcher())
    with pytest.raises(TypeError):
        provider.prices(TICKER, _at(10))


class _FakeYFTicker:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.kwargs: dict[str, Any] = {}

    def history(self, **kwargs: Any) -> pd.DataFrame:
        self.kwargs = kwargs
        return self.frame


def test_yfinance_adapter_drops_nan_and_zero_volume_rows() -> None:
    idx = pd.DatetimeIndex(
        ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05"], tz="Asia/Kolkata"
    )
    frame = pd.DataFrame(
        {
            "Open": [10.0, float("nan"), 12.0, 13.0],
            "High": [11.0, float("nan"), 13.0, 14.0],
            "Low": [9.0, float("nan"), 11.0, 12.0],
            "Close": [10.5, float("nan"), 12.5, 13.5],
            "Adj Close": [10.5, float("nan"), 12.5, 13.5],
            "Volume": [100, 0, 0, 300],
        },
        index=idx,
    )
    fake = _FakeYFTicker(frame)
    bars = yfinance_fetcher(
        TICKER, date(2026, 2, 2), date(2026, 2, 5), ticker_factory=lambda _t: fake
    )
    assert [b.date for b in bars] == [date(2026, 2, 2), date(2026, 2, 5)]
    assert bars[0].close == 10.5
    # yfinance's `end` is exclusive, so the adapter asks for one extra day
    assert fake.kwargs["end"] == date(2026, 2, 6)
    assert fake.kwargs["auto_adjust"] is False


@pytest.mark.live
def test_live_yfinance_smoke(tmp_path: Path) -> None:
    provider = PriceProvider(yfinance_fetcher, tmp_path)
    as_of = datetime(2025, 6, 30, 16, 0, tzinfo=IST)
    bars = provider.prices(TICKER, as_of=as_of)
    assert bars
    assert bars[-1].date <= date(2025, 6, 30)
