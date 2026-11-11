"""S1.5: fundamentals for current-date runs; a typed Withheld, with a reason, for past dates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ta_in.data import IST
from ta_in.fundamentals import (
    FundamentalsData,
    FundamentalsProvider,
    FundamentalsService,
    FundamentalsUnavailableError,
    Statements,
    Withheld,
)

TICKER = "RELIANCE.NS"
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
AS_OF_NOW = datetime(2026, 11, 11, 14, 30, tzinfo=IST)  # same moment as NOW, in IST
AS_OF_PAST = datetime(2025, 6, 30, 16, 0, tzinfo=IST)


def _statements() -> Statements:
    return Statements(
        income_statement={"2025-03-31": {"Total Revenue": 100.0, "Net Income": 10.0}},
        balance_sheet={"2025-03-31": {"Total Assets": 500.0}},
        cash_flow={"2025-03-31": {"Operating Cash Flow": 25.0, "Capex": None}},
    )


class FakeProvider:
    def __init__(self, name: str = "Fake", result: Statements | Exception | None = None) -> None:
        self.name = name
        self._result = _statements() if result is None else result
        self.calls: list[tuple[str, datetime]] = []

    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> Statements:
        self.calls.append((ticker, as_of))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _service(*providers: FundamentalsProvider, **kw: object) -> FundamentalsService:
    return FundamentalsService(list(providers), clock=lambda: NOW, **kw)  # type: ignore[arg-type]


# --- criterion 1: a past-dated call returns Withheld, never statements -------------------


def test_past_dated_call_returns_withheld_with_a_reason() -> None:
    svc = _service(FakeProvider())
    out = svc.get_fundamentals(TICKER, as_of=AS_OF_PAST)
    assert isinstance(out, Withheld)
    assert out.ticker == TICKER
    assert out.as_of == AS_OF_PAST
    assert "past" in out.reason.lower()


def test_past_dated_call_never_reaches_a_provider() -> None:
    provider = FakeProvider()
    _service(provider).get_fundamentals(TICKER, as_of=AS_OF_PAST)
    assert provider.calls == []


def test_the_current_window_is_inclusive_and_a_little_older_is_withheld() -> None:
    svc = _service(FakeProvider(), max_age=timedelta(days=2))
    inside = svc.get_fundamentals(TICKER, as_of=NOW - timedelta(days=2))
    outside = svc.get_fundamentals(TICKER, as_of=NOW - timedelta(days=2, minutes=1))
    assert isinstance(inside, FundamentalsData)
    assert isinstance(outside, Withheld)


# --- criterion 2: a current-date call returns the three statements -----------------------


def test_current_date_call_returns_income_balance_and_cash_flow() -> None:
    provider = FakeProvider()
    out = _service(provider).get_fundamentals(TICKER, as_of=AS_OF_NOW)
    assert isinstance(out, FundamentalsData)
    assert out.ticker == TICKER
    assert out.as_of == AS_OF_NOW
    assert out.fetched_at == NOW
    assert out.income_statement["2025-03-31"]["Total Revenue"] == 100.0
    assert out.balance_sheet["2025-03-31"]["Total Assets"] == 500.0
    assert out.cash_flow["2025-03-31"]["Operating Cash Flow"] == 25.0
    assert provider.calls == [(TICKER, AS_OF_NOW)]


def test_a_future_as_of_is_not_a_past_date() -> None:
    out = _service(FakeProvider()).get_fundamentals(TICKER, as_of=NOW + timedelta(hours=3))
    assert isinstance(out, FundamentalsData)


def test_result_serialises_to_json_and_back() -> None:
    out = _service(FakeProvider()).get_fundamentals(TICKER, as_of=AS_OF_NOW)
    assert isinstance(out, FundamentalsData)
    again = FundamentalsData.model_validate_json(out.model_dump_json())
    assert again == out
    assert again.cash_flow["2025-03-31"]["Capex"] is None


def test_ticker_is_stripped_and_upper_cased() -> None:
    provider = FakeProvider()
    _service(provider).get_fundamentals("  reliance.ns ", as_of=AS_OF_NOW)
    assert provider.calls[0][0] == "RELIANCE.NS"


# --- failures are an explicit Withheld, not a crash --------------------------------------


def test_failing_provider_falls_through_to_the_next() -> None:
    bad = FakeProvider("Bad", RuntimeError("boom"))
    good = FakeProvider("Good")
    out = _service(bad, good).get_fundamentals(TICKER, as_of=AS_OF_NOW)
    assert isinstance(out, FundamentalsData)
    assert len(bad.calls) == 1


def test_all_providers_failing_gives_withheld_naming_each_failure() -> None:
    a = FakeProvider("A", RuntimeError("boom"))
    b = FakeProvider("B", FundamentalsUnavailableError("nothing published"))
    out = _service(a, b).get_fundamentals(TICKER, as_of=AS_OF_NOW)
    assert isinstance(out, Withheld)
    assert "A: RuntimeError: boom" in out.reason
    assert "B: FundamentalsUnavailableError: nothing published" in out.reason


def test_empty_statements_are_withheld_not_returned() -> None:
    empty = Statements(income_statement={}, balance_sheet={}, cash_flow={})
    out = _service(FakeProvider(result=empty)).get_fundamentals(TICKER, as_of=AS_OF_NOW)
    assert isinstance(out, Withheld)


# --- input checks ------------------------------------------------------------------------


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError):
        _service(FakeProvider()).get_fundamentals(TICKER, as_of=datetime(2026, 11, 11, 14, 30))


def test_service_requires_a_provider() -> None:
    with pytest.raises(ValueError):
        FundamentalsService([])


def test_models_require_timezone_aware_datetimes() -> None:
    with pytest.raises(ValueError):
        Withheld(ticker=TICKER, as_of=datetime(2025, 1, 1), reason="x")
