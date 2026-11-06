"""S1.6: company identity (name, sector, exchange) resolved from the ticker and cached."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ta_in.data import (
    IST,
    CompanyIdentity,
    IdentityProvider,
    UnknownTickerError,
    load_sector_map,
    make_kite_identity_resolver,
)

AS_OF = datetime(2026, 2, 10, 16, 0, tzinfo=IST)


class FakeKite:
    def __init__(self) -> None:
        self.instrument_calls = 0
        self.rows: list[dict[str, Any]] = [
            {
                "instrument_token": 738561,
                "tradingsymbol": "RELIANCE",
                "name": "RELIANCE INDUSTRIES",
                "exchange": "NSE",
                "instrument_type": "EQ",
            },
            # same symbol on a non-equity instrument must be ignored
            {
                "instrument_token": 5,
                "tradingsymbol": "RELIANCE",
                "name": "RELIANCE BOND",
                "exchange": "NSE",
                "instrument_type": "BE",
            },
            {
                "instrument_token": 2953217,
                "tradingsymbol": "TCS",
                "name": "",
                "exchange": "NSE",
                "instrument_type": "EQ",
            },
            {
                "instrument_token": 9,
                "tradingsymbol": "NOSECTOR",
                "name": "NO SECTOR LTD",
                "exchange": "NSE",
                "instrument_type": "EQ",
            },
        ]

    def instruments(self, exchange: str) -> list[dict[str, Any]]:
        self.instrument_calls += 1
        assert exchange == "NSE"
        return self.rows


SECTORS = {"RELIANCE": "Energy", "TCS": "Information Technology"}


class CountingResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, ticker: str) -> CompanyIdentity:
        self.calls.append(ticker)
        if ticker != "RELIANCE.NS":
            raise UnknownTickerError(f"{ticker} not found")
        return CompanyIdentity(
            ticker=ticker, name="RELIANCE INDUSTRIES", sector="Energy", exchange="NSE"
        )


def test_same_ticker_gives_same_identity_and_is_resolved_once(tmp_path: Path) -> None:
    resolver = CountingResolver()
    provider = IdentityProvider(resolver, tmp_path)
    first = provider.identity("RELIANCE.NS", as_of=AS_OF)
    second = provider.identity("RELIANCE.NS", as_of=AS_OF)
    assert first == second
    assert len(resolver.calls) == 1


def test_cache_survives_a_new_provider_instance(tmp_path: Path) -> None:
    resolver = CountingResolver()
    first = IdentityProvider(resolver, tmp_path).identity("RELIANCE.NS", as_of=AS_OF)
    second = IdentityProvider(resolver, tmp_path).identity("RELIANCE.NS", as_of=AS_OF)
    assert first == second
    assert len(resolver.calls) == 1


def test_ticker_spelling_does_not_change_the_identity(tmp_path: Path) -> None:
    provider = IdentityProvider(CountingResolver(), tmp_path)
    ids = {
        provider.identity(t, as_of=AS_OF) for t in ("RELIANCE.NS", "reliance.ns", " RELIANCE.NS ")
    }
    assert len(ids) == 1


def test_different_as_of_gives_the_same_identity(tmp_path: Path) -> None:
    provider = IdentityProvider(CountingResolver(), tmp_path)
    early = provider.identity("RELIANCE.NS", as_of=datetime(2020, 1, 1, 9, 0, tzinfo=IST))
    late = provider.identity("RELIANCE.NS", as_of=AS_OF)
    assert early == late


def test_unknown_ticker_fails_on_first_call_and_is_not_cached(tmp_path: Path) -> None:
    resolver = CountingResolver()
    provider = IdentityProvider(resolver, tmp_path)
    with pytest.raises(UnknownTickerError):
        provider.identity("NOSUCH.NS", as_of=AS_OF)
    with pytest.raises(UnknownTickerError):
        provider.identity("NOSUCH.NS", as_of=AS_OF)
    assert resolver.calls == ["NOSUCH.NS", "NOSUCH.NS"]
    assert not list(tmp_path.rglob("NOSUCH*"))


def test_non_ns_ticker_and_naive_as_of_are_rejected(tmp_path: Path) -> None:
    provider = IdentityProvider(CountingResolver(), tmp_path)
    with pytest.raises(ValueError, match=r"\.NS"):
        provider.identity("AAPL", as_of=AS_OF)
    with pytest.raises(ValueError, match="timezone-aware"):
        provider.identity("RELIANCE.NS", as_of=datetime(2026, 2, 10, 16, 0))


def test_corrupt_cache_file_is_ignored_and_re_resolved(tmp_path: Path) -> None:
    resolver = CountingResolver()
    provider = IdentityProvider(resolver, tmp_path)
    provider.identity("RELIANCE.NS", as_of=AS_OF)
    (cache_file,) = tmp_path.rglob("RELIANCE.NS.json")
    cache_file.write_text("{not json", encoding="utf-8")
    again = provider.identity("RELIANCE.NS", as_of=AS_OF)
    assert again.name == "RELIANCE INDUSTRIES"
    assert len(resolver.calls) == 2


def test_kite_resolver_maps_name_exchange_and_sector() -> None:
    resolve = make_kite_identity_resolver(FakeKite(), SECTORS)
    assert resolve("RELIANCE.NS") == CompanyIdentity(
        ticker="RELIANCE.NS", name="RELIANCE INDUSTRIES", sector="Energy", exchange="NSE"
    )


def test_kite_resolver_falls_back_to_symbol_when_name_is_blank() -> None:
    assert make_kite_identity_resolver(FakeKite(), SECTORS)("TCS.NS").name == "TCS"


def test_kite_resolver_gives_none_sector_when_not_in_the_map() -> None:
    identity = make_kite_identity_resolver(FakeKite(), SECTORS)("NOSECTOR.NS")
    assert identity.sector is None
    assert identity.name == "NO SECTOR LTD"


def test_kite_resolver_rejects_unknown_symbol_and_loads_instruments_once() -> None:
    client = FakeKite()
    resolve = make_kite_identity_resolver(client, SECTORS)
    resolve("RELIANCE.NS")
    resolve("TCS.NS")
    with pytest.raises(UnknownTickerError, match="NOSUCH"):
        resolve("NOSUCH.NS")
    assert client.instrument_calls == 1


def test_load_sector_map_parses_csv_and_upper_cases_symbols(tmp_path: Path) -> None:
    path = tmp_path / "sectors.csv"
    path.write_text(
        "symbol,sector\nreliance,Energy\nTCS,Information Technology\n", encoding="utf-8"
    )
    assert load_sector_map(path) == {"RELIANCE": "Energy", "TCS": "Information Technology"}


def test_load_sector_map_rejects_duplicates_and_bad_files(tmp_path: Path) -> None:
    dup = tmp_path / "dup.csv"
    dup.write_text("symbol,sector\nTCS,IT\ntcs,IT\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_sector_map(dup)
    bad = tmp_path / "bad.csv"
    bad.write_text("ticker,industry\nTCS,IT\n", encoding="utf-8")
    with pytest.raises(ValueError, match="symbol,sector"):
        load_sector_map(bad)
    with pytest.raises(ValueError, match="not found"):
        load_sector_map(tmp_path / "missing.csv")


def test_packaged_sector_map_loads() -> None:
    sectors = load_sector_map()
    assert sectors["RELIANCE"]
