"""Live data: one factory that wires the real sources, and a provider that serves them.

The pipeline only needs four things from a data source (identity, prices, news, fundamentals), each
taking `as_of`. `FrozenDataProvider` serves them from saved files; `LiveDataProvider` serves them
from the real sources: Kite (identity and prices), Currents (news) and bfinance (fundamentals).
`make_live_sources` is the single place those sources are wired, and `freeze-fixtures` uses it too.

Rules this layer keeps:
- Every method needs a timezone-aware `as_of`, and the data layer never reads the clock itself:
  the clock is injected, so "now" always comes from the caller.
- A later-dated record is a bug in a source, not something to filter away: it raises
  `PointInTimeError` (the services filter first; this is the second line of defence).
- A failing source is explicit. News and fundamentals keep the services' behaviour (an empty list
  with a logged event, `Withheld` with the reasons). Prices and identity, which a run cannot do
  without, raise `LiveSourceError` naming the source and the cause. An unknown ticker keeps raising
  `UnknownTickerError` so a run stops before any agent starts.
- Missing Kite credentials are a `LiveConfigError` naming every missing variable (never a value).
  A missing Currents key is not fatal: news becomes an explicit gap and the source is listed in
  `degraded`.
- Market data only: no order method is used anywhere (paper trading only).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ta_in.config import Settings
from ta_in.data.identity import (
    CompanyIdentity,
    IdentityProvider,
    UnknownTickerError,
    load_sector_map,
)
from ta_in.data.kite import (
    kite_client_from_settings,
    make_kite_fetcher,
    make_kite_identity_resolver,
)
from ta_in.data.point_in_time import PointInTimeError, check_not_after, guard_records
from ta_in.data.price_cache import PriceBar
from ta_in.data.prices import PriceProvider
from ta_in.fixtures.freeze import FundamentalsSource, IdentitySource, PricesSource
from ta_in.fundamentals import FundamentalsData, FundamentalsService, Withheld
from ta_in.fundamentals.adapters import BFinanceFundamentalsProvider
from ta_in.news import NewsItem, NewsService
from ta_in.news.adapters import CurrentsNewsProvider
from ta_in.news.service import EventSink

MAX_REASON_CHARS = 300


class LiveConfigError(Exception):
    """Credentials the live sources need are not set. Names only, never values."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "Live mode needs credentials that are not set: "
            + ", ".join(missing)
            + " (environment or .env)"
        )


class LiveSourceError(Exception):
    """A source a run cannot do without (`identity` or `prices`) failed."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source}: {reason}")


def _source_error(source: str, err: Exception) -> LiveSourceError:
    if type(err).__name__ == "TokenException":  # kiteconnect's, matched without importing it
        reason = "Kite access token missing or expired: generate a new one (tokens expire daily)"
    else:
        reason = f"{type(err).__name__}: {err}"[:MAX_REASON_CHARS]
    return LiveSourceError(source, reason)


@dataclass(frozen=True)
class LiveSources:
    """The wired sources. `degraded` lists sources that run without data, for example news
    without a Currents key; callers print it."""

    identity: IdentitySource
    prices: PricesSource
    make_news_service: Callable[[EventSink | None], NewsService]
    fundamentals: FundamentalsSource
    degraded: tuple[str, ...] = ()


class _MissingCurrents:
    """Stands in for Currents when no key is set: it fails, so the news service logs an explicit
    `news.provider_failed` gap instead of the run quietly having no news."""

    name = "Currents"

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        raise RuntimeError("CURRENTS_API_KEY not set")


def missing_credentials(settings: Settings) -> list[str]:
    """The required live credentials that are not set, as variable names (never values).

    Kite is required; a missing Currents key is not an error (news becomes an explicit gap)."""
    return [
        name
        for name, value in (
            ("TA_KITE_API_KEY", settings.kite_api_key),
            ("TA_KITE_ACCESS_TOKEN", settings.kite_access_token),
        )
        if value is None or not value.get_secret_value()
    ]


def make_live_sources(settings: Settings, *, clock: Callable[[], datetime]) -> LiveSources:
    """Wire the real sources from `settings`. Constructing them makes no network call.

    `clock` returns a timezone-aware "now"; it decides what counts as a current date for
    fundamentals and how far back Currents can be asked.
    """
    missing = missing_credentials(settings)
    if missing:
        raise LiveConfigError(missing)
    kite = kite_client_from_settings(settings)

    identity = IdentityProvider(
        make_kite_identity_resolver(kite, load_sector_map()), settings.data_cache_dir
    )
    prices = PriceProvider(make_kite_fetcher(kite), settings.data_cache_dir, source="kite")

    def company_name(ticker: str) -> str | None:
        return identity.identity(ticker, as_of=clock()).name

    degraded: list[str] = []
    key = settings.currents_api_key
    news_provider: CurrentsNewsProvider | _MissingCurrents
    if key is not None and key.get_secret_value():
        news_provider = CurrentsNewsProvider(
            key.get_secret_value(),
            name_resolver=company_name,
            clock=lambda: clock().astimezone(UTC),
        )
    else:
        news_provider = _MissingCurrents()
        degraded.append("news: CURRENTS_API_KEY not set")

    def make_news_service(sink: EventSink | None) -> NewsService:
        return NewsService([news_provider], event_sink=sink)

    fundamentals = FundamentalsService([BFinanceFundamentalsProvider()], clock=clock)
    return LiveSources(
        identity=identity,
        prices=prices,
        make_news_service=make_news_service,
        fundamentals=fundamentals,
        degraded=tuple(degraded),
    )


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")


def _check_periods(data: FundamentalsData, as_of: datetime) -> None:
    """A statement period dated after `as_of` can only be a leak from the source."""
    for name, statement in (
        ("income statement", data.income_statement),
        ("balance sheet", data.balance_sheet),
        ("cash flow", data.cash_flow),
    ):
        for period in statement:
            try:
                end = date.fromisoformat(period[:10])
            except ValueError:
                continue  # a label that is not a date cannot be checked
            check_not_after(end, as_of, what=f"{name} period {period}")


class LiveDataProvider:
    """`DataProvider` over the live sources. See the module docstring for the rules it keeps."""

    def __init__(self, sources: LiveSources, *, event_sink: EventSink | None = None) -> None:
        self._sources = sources
        self._news = sources.make_news_service(event_sink)

    @property
    def degraded(self) -> tuple[str, ...]:
        return self._sources.degraded

    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity:
        _require_aware(as_of)
        try:
            return self._sources.identity.identity(ticker, as_of=as_of)
        except (
            ValueError
        ):  # includes UnknownTickerError and an unsupported ticker: the user's input
            raise
        except Exception as err:
            raise _source_error("identity", err) from err

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        _require_aware(as_of)
        try:
            bars = list(self._sources.prices.prices(ticker, as_of=as_of))
        except PointInTimeError:
            raise
        except Exception as err:
            raise _source_error("prices", err) from err
        return guard_records(bars, as_of, lambda b: b.date)

    def news(self, ticker: str, *, as_of: datetime) -> Sequence[NewsItem]:
        _require_aware(as_of)
        items = self._news.get_news(ticker, as_of=as_of)
        return guard_records(items, as_of, lambda i: i.published_time)

    def fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld:
        _require_aware(as_of)
        result = self._sources.fundamentals.get_fundamentals(ticker, as_of=as_of)
        if isinstance(result, FundamentalsData):
            _check_periods(result, as_of)
        return result


__all__ = [
    "LiveConfigError",
    "LiveDataProvider",
    "LiveSourceError",
    "LiveSources",
    "UnknownTickerError",
    "make_live_sources",
    "missing_credentials",
]
