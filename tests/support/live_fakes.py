"""Shared fakes for the live-mode tests: fake identity, prices, news and bfinance (offline)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from ta_in.data import IST, CompanyIdentity, PriceBar
from ta_in.fundamentals import FundamentalsData, FundamentalsService
from ta_in.fundamentals.models import Statements
from ta_in.live import LiveSources
from ta_in.news import NewsItem, NewsService

AS_OF = datetime(2026, 11, 13, 16, 0, tzinfo=IST)
TICKER = "INFY.NS"


def clock() -> datetime:
    return AS_OF


def _bar(d: date, close: float = 1100.0) -> PriceBar:
    return PriceBar(
        date=d,
        open=close - 5,
        high=close + 5,
        low=close - 10,
        close=close,
        adj_close=close,
        volume=1000,
    )


def _bars(last: date, n: int = 30) -> list[PriceBar]:
    return [_bar(last - timedelta(days=i), 1100.0 + i) for i in range(n - 1, -1, -1)]


def _news_item(published: datetime, title: str = "Infosys wins deal") -> NewsItem:
    return NewsItem(
        title=title,
        source="Wire",
        published_time=published,
        url=f"https://example.com/{title.replace(' ', '-')}",
        summary="s",
    )


class FakeIdentity:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity:
        if self.error is not None:
            raise self.error
        return CompanyIdentity(
            ticker=ticker, name="INFOSYS", sector="Information Technology", exchange="NSE"
        )


class FakePrices:
    def __init__(self, bars: Sequence[PriceBar] = (), error: Exception | None = None) -> None:
        self.bars = list(bars)
        self.error = error
        self.calls = 0

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.bars


class FakeNewsProvider:
    name = "FakeWire"

    def __init__(self, items: Sequence[NewsItem] = (), error: Exception | None = None) -> None:
        self.items = list(items)
        self.error = error

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        if self.error is not None:
            raise self.error
        return list(self.items)


class LeakyNewsService(NewsService):
    """A service that forgot to filter: it returns an item published after as_of."""

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        return [_news_item(as_of + timedelta(hours=3), "Future headline")]


class FakeBFinance:
    name = "bfinance"

    def __init__(self, period: str = "2025-03-31", error: Exception | None = None) -> None:
        self.period = period
        self.error = error
        self.calls = 0

    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> Statements:
        self.calls += 1
        if self.error is not None:
            raise self.error
        row = {"Total Revenue": 1e11}
        return Statements(
            income_statement={self.period: row},
            balance_sheet={self.period: {"Total Assets": 5e11}},
            cash_flow={self.period: {"Operating Cash Flow": 2e10}},
        )


class LeakyFundamentals:
    """A source that returns a statement for a period that ends after as_of."""

    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData:
        later = (as_of + timedelta(days=200)).date().isoformat()
        return FundamentalsData(
            ticker=ticker,
            as_of=as_of,
            fetched_at=as_of,
            income_statement={later: {"Total Revenue": 1.0}},
            balance_sheet={},
            cash_flow={},
        )


def _sources(
    *,
    identity: Any = None,
    prices: Any = None,
    news: Any = None,
    fundamentals: Any = None,
    degraded: tuple[str, ...] = (),
) -> LiveSources:
    return LiveSources(
        identity=identity or FakeIdentity(),
        prices=prices or FakePrices(_bars(AS_OF.date())),
        make_news_service=news or (lambda sink: NewsService([FakeNewsProvider()], event_sink=sink)),
        fundamentals=fundamentals or FundamentalsService([FakeBFinance()], clock=clock),
        degraded=degraded,
    )
