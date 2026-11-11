"""bfinance adapter: statements become plain JSON-safe dicts; empty or failing fetches raise."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Any

import pytest

from ta_in.data import IST
from ta_in.fundamentals import FundamentalsUnavailableError
from ta_in.fundamentals.adapters.bfinance import (
    BFinanceFundamentalsProvider,
    normalise_statement,
)

AS_OF = datetime(2026, 11, 11, 14, 30, tzinfo=IST)


class FakeTicker:
    """Mimics bfinance.Ticker.get_*(as_dict=True): {period: {line item: value}}."""

    def __init__(
        self, income: Any = None, balance: Any = None, cash: Any = None, *, fail: bool = False
    ) -> None:
        self.income, self.balance, self.cash, self.fail = income, balance, cash, fail
        self.asked: list[str] = []

    def get_income_stmt(self, as_dict: bool = False, freq: str = "yearly") -> Any:
        self.asked.append(f"income:{freq}:{as_dict}")
        if self.fail:
            raise ConnectionError("blocked")
        return self.income or {}

    def get_balance_sheet(self, as_dict: bool = False, freq: str = "yearly") -> Any:
        self.asked.append(f"balance:{freq}:{as_dict}")
        return self.balance or {}

    def get_cash_flow(self, as_dict: bool = False, freq: str = "yearly") -> Any:
        self.asked.append(f"cash:{freq}:{as_dict}")
        return self.cash or {}


def _full() -> FakeTicker:
    return FakeTicker(
        income={datetime(2025, 3, 31): {"Total Revenue": 100, "Net Income": 10.5}},
        balance={datetime(2025, 3, 31): {"Total Assets": 500}},
        cash={datetime(2025, 3, 31): {"Operating Cash Flow": 25.0}},
    )


def test_normalise_converts_dates_nan_and_numbers() -> None:
    raw = {
        datetime(2025, 3, 31, 0, 0): {"Revenue": 100, "Profit": float("nan"), "Odd": float("inf")},
        date(2024, 3, 31): {"Revenue": 90.5, "Note": "n/a", "Missing": None},
    }
    out = normalise_statement(raw)
    assert out == {
        "2025-03-31": {"Revenue": 100.0, "Profit": None, "Odd": None},
        "2024-03-31": {"Revenue": 90.5, "Note": None, "Missing": None},
    }
    json.dumps(out)  # must not raise: JSON-safe (no NaN, no Timestamp)
    assert not any(isinstance(v, float) and math.isnan(v) for p in out.values() for v in p.values())


def test_normalise_handles_empty_and_none() -> None:
    assert normalise_statement({}) == {}
    assert normalise_statement(None) == {}


def test_adapter_returns_three_yearly_statements() -> None:
    fake = _full()
    provider = BFinanceFundamentalsProvider(ticker_factory=lambda t: fake)
    out = provider.get_fundamentals("RELIANCE.NS", as_of=AS_OF)
    assert out.income_statement == {"2025-03-31": {"Total Revenue": 100.0, "Net Income": 10.5}}
    assert out.balance_sheet == {"2025-03-31": {"Total Assets": 500.0}}
    assert out.cash_flow == {"2025-03-31": {"Operating Cash Flow": 25.0}}
    assert fake.asked == ["income:yearly:True", "balance:yearly:True", "cash:yearly:True"]


def test_adapter_passes_the_ticker_through_unchanged() -> None:
    seen: list[str] = []

    def factory(t: str) -> FakeTicker:
        seen.append(t)
        return _full()

    BFinanceFundamentalsProvider(ticker_factory=factory).get_fundamentals("TCS.NS", as_of=AS_OF)
    assert seen == ["TCS.NS"]


def test_all_empty_statements_raise_unavailable() -> None:
    provider = BFinanceFundamentalsProvider(ticker_factory=lambda t: FakeTicker())
    with pytest.raises(FundamentalsUnavailableError):
        provider.get_fundamentals("TCS.NS", as_of=AS_OF)


def test_a_partly_empty_result_is_still_returned() -> None:
    fake = FakeTicker(income={datetime(2025, 3, 31): {"Total Revenue": 1}})
    out = BFinanceFundamentalsProvider(ticker_factory=lambda t: fake).get_fundamentals(
        "TCS.NS", as_of=AS_OF
    )
    assert out.balance_sheet == {}
    assert out.income_statement


def test_fetch_errors_propagate_so_the_service_can_withhold() -> None:
    provider = BFinanceFundamentalsProvider(ticker_factory=lambda t: FakeTicker(fail=True))
    with pytest.raises(ConnectionError):
        provider.get_fundamentals("TCS.NS", as_of=AS_OF)


def test_adapter_rejects_naive_as_of() -> None:
    provider = BFinanceFundamentalsProvider(ticker_factory=lambda t: _full())
    with pytest.raises(ValueError):
        provider.get_fundamentals("TCS.NS", as_of=datetime(2026, 11, 11, 14, 30))


# --- live smoke test (opt-in, network) ---------------------------------------------------


@pytest.mark.live
def test_live_bfinance_smoke() -> None:
    out = BFinanceFundamentalsProvider().get_fundamentals("RELIANCE.NS", as_of=datetime.now(UTC))
    assert out.income_statement
