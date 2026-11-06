"""Zerodha Kite Connect as a price source. Market-data reads only: no order methods are used."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from ta_in.config.settings import Settings
from ta_in.data.identity import CompanyIdentity, Resolver, UnknownTickerError
from ta_in.data.point_in_time import IST
from ta_in.data.price_cache import PriceBar
from ta_in.data.prices import Fetcher

# Kite allows at most 2000 days per daily-candle request.
MAX_DAYS_PER_REQUEST = 2000


class KiteConfigError(Exception):
    """Kite credentials are missing. Names the variables, never their values."""


class KiteInstruments(Protocol):
    """The read-only instrument listing, enough for identity lookups."""

    def instruments(self, exchange: str) -> list[dict[str, Any]]: ...


class KiteMarketData(KiteInstruments, Protocol):
    """The two read-only KiteConnect methods used here."""

    def historical_data(
        self, instrument_token: int, from_date: date, to_date: date, interval: str
    ) -> list[dict[str, Any]]: ...


def kite_client_from_settings(settings: Settings) -> KiteMarketData:
    """Build a KiteConnect client from `TA_KITE_API_KEY` / `TA_KITE_ACCESS_TOKEN`."""
    missing = [
        name
        for name, value in (
            ("TA_KITE_API_KEY", settings.kite_api_key),
            ("TA_KITE_ACCESS_TOKEN", settings.kite_access_token),
        )
        if value is None or not value.get_secret_value()
    ]
    if missing:
        raise KiteConfigError("Kite credentials not set: " + ", ".join(missing))
    assert settings.kite_api_key is not None and settings.kite_access_token is not None

    from kiteconnect import KiteConnect

    client = KiteConnect(api_key=settings.kite_api_key.get_secret_value())
    client.set_access_token(settings.kite_access_token.get_secret_value())
    client_typed: KiteMarketData = client
    return client_typed


def make_kite_identity_resolver(client: KiteInstruments, sector_map: dict[str, str]) -> Resolver:
    """Resolver for `IdentityProvider`: name and exchange from Kite, sector from `sector_map`.

    Kite has no sector field, so a symbol missing from the map gets `sector=None`.
    """
    by_symbol: dict[str, dict[str, Any]] = {}

    def load() -> None:
        rows = [r for r in client.instruments("NSE") if r.get("instrument_type") == "EQ"]
        # lowest instrument_token wins if a symbol repeats, so the answer is deterministic
        for row in sorted(rows, key=lambda r: int(r["instrument_token"]), reverse=True):
            by_symbol[str(row["tradingsymbol"])] = row

    def resolve(ticker: str) -> CompanyIdentity:
        if not by_symbol:
            load()
        symbol = ticker.removesuffix(".NS")
        row = by_symbol.get(symbol)
        if row is None:
            raise UnknownTickerError(f"{symbol!r} not found among NSE equities on Kite")
        return CompanyIdentity(
            ticker=ticker,
            name=str(row.get("name") or "").strip() or symbol,
            sector=sector_map.get(symbol),
            exchange="NSE",
        )

    return resolve


def make_kite_fetcher(client: KiteMarketData) -> Fetcher:
    """Fetcher for `PriceProvider`: daily bars for `<SYMBOL>.NS` from Kite.

    Kite has no adjusted close, so `adj_close` equals `close`. Whether its history is adjusted
    for splits and bonuses is not verified.
    """
    tokens: dict[str, int] = {}

    def token_for(symbol: str) -> int:
        if not tokens:
            for row in client.instruments("NSE"):
                tokens[str(row["tradingsymbol"])] = int(row["instrument_token"])
        if symbol not in tokens:
            raise ValueError(f"{symbol!r} not found among NSE instruments on Kite")
        return tokens[symbol]

    def fetch(ticker: str, start: date, end: date) -> list[PriceBar]:
        token = token_for(ticker.removesuffix(".NS"))
        bars: list[PriceBar] = []
        lo = start
        while lo <= end:
            hi = min(lo + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
            for row in client.historical_data(token, lo, hi, "day"):
                volume = int(row["volume"])
                if volume == 0:
                    continue
                stamp = row["date"]
                day = stamp.astimezone(IST).date() if stamp.tzinfo else stamp.date()
                bars.append(
                    PriceBar(
                        date=day,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        adj_close=float(row["close"]),
                        volume=volume,
                    )
                )
            lo = hi + timedelta(days=1)
        return bars

    return fetch
