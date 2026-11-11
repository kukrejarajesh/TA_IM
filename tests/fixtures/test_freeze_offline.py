"""S1.8 freeze logic with fake providers: what gets written, and what is refused or recorded."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ta_in.data import IST, PriceBar
from ta_in.fixtures import (
    FixtureExistsError,
    FixtureSet,
    FrozenDataProvider,
    Manifest,
    freeze_fixtures,
)
from ta_in.fixtures.models import cell_path, identity_path, prices_path
from ta_in.fundamentals import FundamentalsData, Withheld
from ta_in.news import NewsItem, NewsService

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=IST)
FIXTURE_SET = FixtureSet(
    tickers=["AAA.NS", "BBB.NS"],
    past_dates=[date(2026, 10, 21), date(2026, 10, 28)],
)


def _bars(first: date, last: date) -> list[PriceBar]:
    out, d, px = [], first, 100.0
    while d <= last:
        if d.weekday() < 5:
            out.append(
                PriceBar(
                    date=d, open=px, high=px + 1, low=px - 1, close=px, adj_close=px, volume=10
                )
            )
            px += 1
        d += timedelta(days=1)
    return out


class FakePrices:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[tuple[str, datetime]] = []

    def prices(self, ticker: str, *, as_of: datetime) -> list[PriceBar]:
        self.calls.append((ticker, as_of))
        if ticker in self.fail:
            raise ConnectionError("kite down")
        return _bars(date(2025, 11, 1), as_of.astimezone(IST).date())


class FakeIdentity:
    def identity(self, ticker: str, *, as_of: datetime):  # type: ignore[no-untyped-def]
        from ta_in.data import CompanyIdentity

        return CompanyIdentity(ticker=ticker, name=f"{ticker} Ltd", sector="IT", exchange="NSE")


class FakeNewsProvider:
    name = "FakeNews"

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        return [
            NewsItem(
                title=f"{ticker} early",
                source="Wire",
                published_time=as_of - timedelta(days=1),
                url=f"https://x.test/{ticker}/early",
            ),
            NewsItem(
                title=f"{ticker} late",
                source="Wire",
                published_time=as_of + timedelta(days=1),
                url=f"https://x.test/{ticker}/late",
            ),
        ]


def _news_factory(sink):  # type: ignore[no-untyped-def]
    return NewsService([FakeNewsProvider()], event_sink=sink)


class FakeFundamentals:
    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld:
        if as_of < NOW - timedelta(days=2):
            return Withheld(ticker=ticker, as_of=as_of, reason="Past date: withheld")
        return FundamentalsData(
            ticker=ticker,
            as_of=as_of,
            fetched_at=NOW.astimezone(UTC),
            income_statement={"2026-03-31": {"Total Revenue": 1.0}},
            balance_sheet={"2026-03-31": {"Total Assets": 2.0}},
            cash_flow={"2026-03-31": {"Operating Cash Flow": 3.0}},
        )


def _freeze(out: Path, *, prices: FakePrices | None = None, force: bool = False) -> Manifest:
    return freeze_fixtures(
        FIXTURE_SET,
        out,
        prices=prices or FakePrices(),
        identity=FakeIdentity(),
        make_news_service=_news_factory,
        fundamentals=FakeFundamentals(),
        now=NOW,
        sources={"prices": "fake", "news": "fake", "fundamentals": "fake", "identity": "fake"},
        force=force,
    )


def test_writes_every_expected_file(tmp_path: Path) -> None:
    manifest = _freeze(tmp_path)
    assert manifest.failures == []
    assert len(manifest.cells) == 2 * 3  # 2 tickers x (2 past dates + current)
    for ticker in FIXTURE_SET.tickers:
        assert prices_path(tmp_path, ticker).exists()
        assert identity_path(tmp_path, ticker).exists()
    for cell in manifest.cells:
        assert cell_path(tmp_path, cell.ticker, cell.as_of).exists()
    assert (tmp_path / "manifest.json").exists()


def test_past_cells_are_withheld_and_the_current_cell_has_statements(tmp_path: Path) -> None:
    manifest = _freeze(tmp_path)
    kinds = {(c.as_of.date(), c.fundamentals) for c in manifest.cells if c.ticker == "AAA.NS"}
    assert (date(2026, 10, 21), "withheld") in kinds
    assert (date(2026, 11, 11), "data") in kinds


def test_news_after_a_cells_as_of_is_not_stored(tmp_path: Path) -> None:
    _freeze(tmp_path)
    provider = FrozenDataProvider(tmp_path)
    as_of = datetime(2026, 10, 21, 16, 0, tzinfo=IST)
    titles = [i.title for i in provider.news("AAA.NS", as_of=as_of)]
    assert titles == ["AAA.NS early"]


def test_prices_file_keeps_later_bars_but_the_provider_never_returns_them(tmp_path: Path) -> None:
    _freeze(tmp_path)
    provider = FrozenDataProvider(tmp_path)
    as_of = datetime(2026, 10, 21, 16, 0, tzinfo=IST)
    assert provider.outcome_prices("AAA.NS")[-1].date > as_of.date()
    assert max(b.date for b in provider.prices("AAA.NS", as_of=as_of)) <= as_of.date()


def test_price_snapshot_is_stored_per_cell(tmp_path: Path) -> None:
    _freeze(tmp_path)
    provider = FrozenDataProvider(tmp_path)
    snap = provider.price_snapshot("AAA.NS", as_of=datetime(2026, 10, 28, 16, 0, tzinfo=IST))
    assert snap is not None
    assert snap.as_of_date == date(2026, 10, 28)


def test_existing_snapshots_are_not_overwritten_without_force(tmp_path: Path) -> None:
    _freeze(tmp_path)
    with pytest.raises(FixtureExistsError):
        _freeze(tmp_path)
    assert _freeze(tmp_path, force=True).failures == []


def test_a_failing_source_is_recorded_not_hidden(tmp_path: Path) -> None:
    manifest = _freeze(tmp_path, prices=FakePrices(fail={"BBB.NS"}))
    assert any("BBB.NS" in f and "kite down" in f for f in manifest.failures)
    assert {c.ticker for c in manifest.cells} == {"AAA.NS"}  # the broken ticker has no cells


def test_manifest_round_trips(tmp_path: Path) -> None:
    manifest = _freeze(tmp_path)
    again = Manifest.model_validate_json((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert again == manifest


def test_fixture_set_rejects_non_nse_or_duplicate_tickers() -> None:
    with pytest.raises(ValueError):
        FixtureSet(tickers=["AAA"], past_dates=[date(2026, 10, 21)])
    with pytest.raises(ValueError):
        FixtureSet(tickers=["AAA.NS", "aaa.ns"], past_dates=[date(2026, 10, 21)])
