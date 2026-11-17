"""S8.1: LiveDataProvider and the live-sources factory, offline (fake Kite, news and bfinance)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ta_in.config import Settings
from ta_in.data import (
    IST,
    CompanyIdentity,
    DataProvider,
    PointInTimeError,
    PriceBar,
    UnknownTickerError,
)
from ta_in.fundamentals import FundamentalsData, FundamentalsService, Withheld
from ta_in.fundamentals.models import Statements
from ta_in.live import (
    LiveConfigError,
    LiveDataProvider,
    LiveSourceError,
    LiveSources,
    make_live_sources,
)
from ta_in.news import NewsItem, NewsService

AS_OF = datetime(2026, 11, 13, 16, 0, tzinfo=IST)
TICKER = "INFY.NS"
SECRET_KEY = "KITE-KEY-SECRET-123"
SECRET_TOKEN = "KITE-TOKEN-SECRET-456"


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


def _provider(**kw: Any) -> tuple[LiveDataProvider, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    provider = LiveDataProvider(
        _sources(**kw), event_sink=lambda name, payload: events.append({"event": name, **payload})
    )
    return provider, events


# --- the interface ---------------------------------------------------------------------


def test_the_live_provider_satisfies_the_data_provider_interface() -> None:
    provider, _ = _provider()
    assert isinstance(provider, DataProvider)


@pytest.mark.parametrize("method", ["identity", "prices", "news", "fundamentals"])
def test_every_method_rejects_a_naive_as_of(method: str) -> None:
    provider, _ = _provider()
    with pytest.raises(ValueError, match="timezone"):
        getattr(provider, method)(TICKER, as_of=datetime(2026, 11, 13, 16, 0))


def test_good_data_passes_through_unchanged() -> None:
    provider, _ = _provider(
        news=lambda sink: NewsService(
            [FakeNewsProvider([_news_item(AS_OF - timedelta(hours=5))])], event_sink=sink
        )
    )
    assert provider.identity(TICKER, as_of=AS_OF).name == "INFOSYS"
    assert provider.prices(TICKER, as_of=AS_OF)[-1].date == AS_OF.date()
    assert [n.title for n in provider.news(TICKER, as_of=AS_OF)] == ["Infosys wins deal"]
    funds = provider.fundamentals(TICKER, as_of=AS_OF)
    assert isinstance(funds, FundamentalsData) and "2025-03-31" in funds.income_statement


# --- criterion 1: a later-dated record raises PointInTimeError --------------------------


def test_a_price_bar_dated_after_as_of_raises_and_is_not_filtered() -> None:
    leaky = _bars(AS_OF.date()) + [_bar(AS_OF.date() + timedelta(days=1))]
    provider, _ = _provider(prices=FakePrices(leaky))
    with pytest.raises(PointInTimeError):
        provider.prices(TICKER, as_of=AS_OF)


def test_a_news_item_published_after_as_of_raises() -> None:
    provider, _ = _provider(
        news=lambda sink: LeakyNewsService([FakeNewsProvider()], event_sink=sink)
    )
    with pytest.raises(PointInTimeError):
        provider.news(TICKER, as_of=AS_OF)


def test_a_statement_period_dated_after_as_of_raises() -> None:
    provider, _ = _provider(fundamentals=LeakyFundamentals())
    with pytest.raises(PointInTimeError):
        provider.fundamentals(TICKER, as_of=AS_OF)


def test_the_real_services_never_hand_the_provider_a_leak() -> None:
    # The services filter by themselves; the provider's guard is the second line of defence.
    late = _news_item(AS_OF + timedelta(hours=2), "Late story")
    ok = _news_item(AS_OF - timedelta(hours=2), "Early story")
    provider, events = _provider(
        news=lambda sink: NewsService([FakeNewsProvider([late, ok])], event_sink=sink)
    )
    assert [n.title for n in provider.news(TICKER, as_of=AS_OF)] == ["Early story"]
    assert any(e["event"] == "news.dropped_after_as_of" for e in events)


# --- criterion 2: a failing or missing source is explicit -------------------------------


def test_a_failing_news_provider_is_an_empty_list_and_an_event_not_a_crash() -> None:
    provider, events = _provider(
        news=lambda sink: NewsService(
            [FakeNewsProvider(error=RuntimeError("down"))], event_sink=sink
        )
    )
    assert provider.news(TICKER, as_of=AS_OF) == []
    failed = [e for e in events if e["event"] == "news.provider_failed"]
    assert failed and "down" in failed[0]["error"]


def test_every_fundamentals_provider_failing_gives_withheld_listing_each_failure() -> None:
    funds = FundamentalsService(
        [FakeBFinance(error=RuntimeError("bfinance exploded"))], clock=clock
    )
    provider, _ = _provider(fundamentals=funds)
    result = provider.fundamentals(TICKER, as_of=AS_OF)
    assert isinstance(result, Withheld)
    assert "bfinance exploded" in result.reason


def test_a_past_date_gives_withheld_and_never_calls_the_provider() -> None:
    bf = FakeBFinance()
    provider, _ = _provider(fundamentals=FundamentalsService([bf], clock=clock))
    result = provider.fundamentals(TICKER, as_of=AS_OF - timedelta(days=10))
    assert isinstance(result, Withheld) and "Past date" in result.reason
    assert bf.calls == 0


def test_a_price_fetch_failure_is_a_typed_error_with_the_cause() -> None:
    provider, _ = _provider(prices=FakePrices(error=ConnectionError("kite unreachable")))
    with pytest.raises(LiveSourceError) as exc:
        provider.prices(TICKER, as_of=AS_OF)
    assert exc.value.source == "prices" and "kite unreachable" in str(exc.value)


def test_a_kite_token_error_says_the_token_is_missing_or_expired() -> None:
    class TokenException(Exception):  # the name is what the provider recognises
        pass

    provider, _ = _provider(prices=FakePrices(error=TokenException("Incorrect api_key")))
    with pytest.raises(LiveSourceError, match="expired") as exc:
        provider.prices(TICKER, as_of=AS_OF)
    assert "generate a new one" in str(exc.value)


def test_an_unknown_ticker_still_raises_unknown_ticker_error() -> None:
    provider, _ = _provider(identity=FakeIdentity(UnknownTickerError("'ZZZ' not found")))
    with pytest.raises(UnknownTickerError):
        provider.identity("ZZZ.NS", as_of=AS_OF)


def test_an_identity_lookup_failure_is_a_typed_error() -> None:
    provider, _ = _provider(identity=FakeIdentity(OSError("instruments call failed")))
    with pytest.raises(LiveSourceError) as exc:
        provider.identity(TICKER, as_of=AS_OF)
    assert exc.value.source == "identity" and "instruments call failed" in str(exc.value)


def test_error_messages_are_trimmed() -> None:
    provider, _ = _provider(prices=FakePrices(error=RuntimeError("x" * 5000)))
    with pytest.raises(LiveSourceError) as exc:
        provider.prices(TICKER, as_of=AS_OF)
    assert len(str(exc.value)) < 400


def test_the_provider_reports_degraded_sources() -> None:
    provider, _ = _provider(degraded=("news: CURRENTS_API_KEY not set",))
    assert provider.degraded == ("news: CURRENTS_API_KEY not set",)


# --- the factory -----------------------------------------------------------------------


class FakeKite:
    def __init__(self) -> None:
        self.history_calls = 0

    def instruments(self, exchange: str) -> list[dict[str, Any]]:
        assert exchange == "NSE"
        return [
            {
                "instrument_type": "EQ",
                "tradingsymbol": "INFY",
                "name": "INFOSYS",
                "instrument_token": 408065,
            },
            {
                "instrument_type": "EQ",
                "tradingsymbol": "TCS",
                "name": "TATA CONSULTANCY",
                "instrument_token": 2953217,
            },
        ]

    def historical_data(
        self, instrument_token: int, from_date: date, to_date: date, interval: str
    ) -> list[dict[str, Any]]:
        self.history_calls += 1
        rows: list[dict[str, Any]] = []
        day = from_date
        while day <= to_date:
            if day.weekday() < 5:
                rows.append(
                    {
                        "date": datetime(day.year, day.month, day.day, tzinfo=IST),
                        "open": 1095.0,
                        "high": 1105.0,
                        "low": 1090.0,
                        "close": 1100.0,
                        "volume": 1000,
                    }
                )
            day += timedelta(days=1)
        return rows


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "ANTHROPIC_API_KEY": "sk-test",
        "data_cache_dir": tmp_path / "cache",
        "kite_api_key": SECRET_KEY,
        "kite_access_token": SECRET_TOKEN,
        "CURRENTS_API_KEY": "currents-secret-789",
    }
    base.update(extra)
    return Settings(**base)  # type: ignore[arg-type]


def test_missing_kite_credentials_are_all_named_and_no_value_is_shown(tmp_path: Path) -> None:
    settings = _settings(tmp_path, kite_api_key=None, kite_access_token=None)
    with pytest.raises(LiveConfigError) as exc:
        make_live_sources(settings, clock=clock)
    assert exc.value.missing == ["TA_KITE_API_KEY", "TA_KITE_ACCESS_TOKEN"]
    text = str(exc.value)
    assert "TA_KITE_API_KEY" in text and "TA_KITE_ACCESS_TOKEN" in text


def test_one_missing_kite_credential_names_only_that_one(tmp_path: Path) -> None:
    with pytest.raises(LiveConfigError) as exc:
        make_live_sources(_settings(tmp_path, kite_access_token=None), clock=clock)
    assert exc.value.missing == ["TA_KITE_ACCESS_TOKEN"]
    assert SECRET_KEY not in str(exc.value)


def test_the_factory_wires_identity_prices_news_and_fundamentals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kite = FakeKite()
    monkeypatch.setattr("ta_in.live.kite_client_from_settings", lambda settings: kite)
    queries: list[str] = []

    def fake_get(
        url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> Any:
        queries.append(params["query"])
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"status": "ok", "news": []}, text=""
        )

    monkeypatch.setattr("requests.get", fake_get)  # the adapter calls requests.get at call time
    sources = make_live_sources(_settings(tmp_path), clock=clock)
    assert sources.degraded == ()
    provider = LiveDataProvider(sources)

    identity = provider.identity(TICKER, as_of=AS_OF)
    assert (identity.name, identity.exchange) == ("INFOSYS", "NSE")
    assert identity.sector == "Information Technology"  # from the sector map

    bars = provider.prices(TICKER, as_of=AS_OF)
    assert bars and bars[-1].date <= AS_OF.date()
    calls = kite.history_calls
    provider.prices(TICKER, as_of=AS_OF)
    assert kite.history_calls == calls  # the second call is served from the cache

    assert provider.news(TICKER, as_of=AS_OF) == []
    assert queries and "INFOSYS" in queries[0].upper()  # the company name reaches the query

    old = provider.fundamentals(TICKER, as_of=AS_OF - timedelta(days=30))
    assert isinstance(old, Withheld)  # the injected clock decides what is "current"


def test_constructing_the_sources_makes_no_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network used while constructing")

    monkeypatch.setattr("ta_in.live.kite_client_from_settings", lambda settings: FakeKite())
    monkeypatch.setattr("requests.get", boom)
    make_live_sources(_settings(tmp_path), clock=clock)


def test_a_missing_currents_key_is_an_explicit_news_gap_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ta_in.live.kite_client_from_settings", lambda settings: FakeKite())
    sources = make_live_sources(_settings(tmp_path, CURRENTS_API_KEY=None), clock=clock)
    assert sources.degraded == ("news: CURRENTS_API_KEY not set",)
    events: list[dict[str, Any]] = []
    provider = LiveDataProvider(
        sources, event_sink=lambda name, payload: events.append({"event": name, **payload})
    )
    assert provider.news(TICKER, as_of=AS_OF) == []
    failed = [e for e in events if e["event"] == "news.provider_failed"]
    assert failed and "CURRENTS_API_KEY not set" in failed[0]["error"]
    # the other sources still work
    assert provider.identity(TICKER, as_of=AS_OF).name == "INFOSYS"
    assert provider.prices(TICKER, as_of=AS_OF)


# --- criterion 3: freeze-fixtures builds its providers through the same factory ----------


def _freeze_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("TA_") or name in {"ANTHROPIC_API_KEY", "CURRENTS_API_KEY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("CURRENTS_API_KEY", "currents-secret-789")
    fixture_set = tmp_path / "fixture_set.json"
    fixture_set.write_text(
        json.dumps({"tickers": [TICKER], "past_dates": [], "include_current": True}),
        encoding="utf-8",
    )
    return fixture_set, tmp_path / "out"


def test_freeze_fixtures_uses_the_shared_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ta_in.fixtures.cli import main

    fixture_set, out = _freeze_env(tmp_path, monkeypatch)
    today = datetime.now(IST).date()
    used: list[Settings] = []

    def fake_factory(settings: Settings, *, clock: Any) -> LiveSources:
        used.append(settings)
        return _sources(
            prices=FakePrices(_bars(today - timedelta(days=1), 70)),
            fundamentals=FundamentalsService([FakeBFinance("2025-03-31")], clock=clock),
        )

    monkeypatch.setattr("ta_in.live.make_live_sources", fake_factory)
    code = main(["--fixture-set", str(fixture_set), "--out", str(out)])
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert len(used) == 1  # the command went through the shared factory
    assert (out / "manifest.json").exists()
    assert (out / "snapshots" / "identity" / f"{TICKER}.json").exists()
    assert "INFY.NS" in captured.out


def test_freeze_fixtures_names_missing_kite_credentials_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ta_in.fixtures.cli import main

    fixture_set, out = _freeze_env(tmp_path, monkeypatch)
    code = main(["--fixture-set", str(fixture_set), "--out", str(out)])
    err = capsys.readouterr().err
    assert code == 2
    assert "TA_KITE_API_KEY" in err and "TA_KITE_ACCESS_TOKEN" in err
    assert not out.exists()  # nothing was written


# --- safety ----------------------------------------------------------------------------


def test_the_live_module_never_mentions_an_order_method() -> None:
    source = (Path(__file__).parents[2] / "src" / "ta_in" / "live.py").read_text("utf-8")
    for name in ("place_order", "modify_order", "cancel_order"):
        assert name not in source
