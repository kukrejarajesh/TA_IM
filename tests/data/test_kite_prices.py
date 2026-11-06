"""Zerodha (Kite Connect) price fetcher: offline tests with a fake client."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ta_in.config.settings import Settings
from ta_in.data import (
    IST,
    KiteConfigError,
    PriceBar,
    PriceProvider,
    kite_client_from_settings,
    make_kite_fetcher,
)

TICKER = "RELIANCE.NS"
TOKEN = 738561


def _row(d: date, close: float = 100.0, volume: int = 1_000) -> dict[str, Any]:
    return {
        "date": datetime(d.year, d.month, d.day, 0, 0, tzinfo=IST),
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": volume,
    }


class FakeKite:
    """Stands in for KiteConnect. Like a careless client it ignores `to_date` on purpose."""

    def __init__(self) -> None:
        self.instrument_calls = 0
        self.history_calls: list[tuple[int, date, date, str]] = []
        self.rows = [_row(date(2026, 2, d), 100.0 + d) for d in (2, 3, 4, 5, 6, 9, 10, 11, 12)]

    def instruments(self, exchange: str) -> list[dict[str, Any]]:
        self.instrument_calls += 1
        assert exchange == "NSE"
        return [
            {"instrument_token": 1, "tradingsymbol": "TCS", "exchange": "NSE"},
            {"instrument_token": TOKEN, "tradingsymbol": "RELIANCE", "exchange": "NSE"},
        ]

    def historical_data(
        self, instrument_token: int, from_date: date, to_date: date, interval: str
    ) -> list[dict[str, Any]]:
        self.history_calls.append((instrument_token, from_date, to_date, interval))
        return [r for r in self.rows if r["date"].date() >= from_date]


def test_ticker_maps_to_instrument_token_once() -> None:
    client = FakeKite()
    fetch = make_kite_fetcher(client)
    fetch(TICKER, date(2026, 2, 1), date(2026, 2, 12))
    fetch(TICKER, date(2026, 2, 1), date(2026, 2, 12))
    assert client.instrument_calls == 1
    assert {c[0] for c in client.history_calls} == {TOKEN}
    assert client.history_calls[0][3] == "day"


def test_rows_become_price_bars_on_ist_dates() -> None:
    client = FakeKite()
    client.rows = [_row(date(2026, 2, 2), 50.0), _row(date(2026, 2, 3), 51.0, volume=0)]
    bars = make_kite_fetcher(client)(TICKER, date(2026, 2, 1), date(2026, 2, 12))
    assert bars == [
        PriceBar(
            date=date(2026, 2, 2),
            open=49.0,
            high=52.0,
            low=48.0,
            close=50.0,
            adj_close=50.0,
            volume=1_000,
        )
    ]


def test_unknown_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="NOSUCH"):
        make_kite_fetcher(FakeKite())("NOSUCH.NS", date(2026, 2, 1), date(2026, 2, 12))


def test_long_range_is_split_into_requests_of_at_most_2000_days() -> None:
    client = FakeKite()
    start, end = date(2020, 1, 1), date(2026, 2, 12)
    make_kite_fetcher(client)(TICKER, start, end)
    assert len(client.history_calls) == 2
    assert client.history_calls[0][1] == start
    assert client.history_calls[-1][2] == end
    for _, lo, hi, _ in client.history_calls:
        assert (hi - lo) <= timedelta(days=1999)


def test_provider_never_returns_bars_after_as_of_and_caches(tmp_path: Path) -> None:
    client = FakeKite()
    provider = PriceProvider(make_kite_fetcher(client), tmp_path, lookback_days=30, source="kite")
    as_of = datetime(2026, 2, 9, 16, 0, tzinfo=IST)
    first = provider.prices(TICKER, as_of=as_of)
    second = provider.prices(TICKER, as_of=as_of)
    assert first and first[-1].date == date(2026, 2, 9)
    assert first == second
    assert len(client.history_calls) == 1


def test_sources_do_not_share_cache(tmp_path: Path) -> None:
    as_of = datetime(2026, 2, 9, 16, 0, tzinfo=IST)

    def other(_t: str, start: date, end: date) -> list[PriceBar]:
        return [
            PriceBar(
                date=date(2026, 2, 2),
                open=1,
                high=1,
                low=1,
                close=1,
                adj_close=1,
                volume=1,
            )
        ]

    PriceProvider(other, tmp_path, lookback_days=30).prices(TICKER, as_of=as_of)
    kite = PriceProvider(
        make_kite_fetcher(FakeKite()), tmp_path, lookback_days=30, source="kite"
    ).prices(TICKER, as_of=as_of)
    assert len(kite) > 1
    assert kite[0].close != 1


def _settings(monkeypatch: pytest.MonkeyPatch, **kite: str) -> Settings:
    for name in ("TA_KITE_API_KEY", "TA_KITE_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None, ANTHROPIC_API_KEY="test", **kite)  # type: ignore[call-arg]


def test_missing_credentials_error_names_variables_but_not_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, kite_api_key="super-secret-key")
    with pytest.raises(KiteConfigError) as exc:
        kite_client_from_settings(settings)
    msg = str(exc.value)
    assert "TA_KITE_ACCESS_TOKEN" in msg
    assert "super-secret-key" not in msg


@pytest.mark.live
@pytest.mark.skipif(
    not (os.environ.get("TA_KITE_API_KEY") and os.environ.get("TA_KITE_ACCESS_TOKEN")),
    reason="needs TA_KITE_API_KEY and TA_KITE_ACCESS_TOKEN in the environment",
)
def test_live_kite_smoke(tmp_path: Path) -> None:
    client = kite_client_from_settings(Settings(_env_file=None))  # type: ignore[call-arg]
    provider = PriceProvider(make_kite_fetcher(client), tmp_path, source="kite")
    bars = provider.prices(TICKER, as_of=datetime(2025, 6, 30, 16, 0, tzinfo=IST))
    assert bars
    assert bars[-1].date <= date(2025, 6, 30)
