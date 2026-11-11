"""bfinance adapter: yearly Ind AS statements for NSE tickers. Current-date only (see provider)."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ta_in.fundamentals.models import Statement, Statements
from ta_in.fundamentals.provider import FundamentalsUnavailableError

log = logging.getLogger(__name__)


def _default_ticker(ticker: str) -> Any:
    """Real factory. The import is deferred so the package loads without bfinance (and pandas)."""
    import bfinance

    return bfinance.Ticker(ticker)


def _period_label(key: Any) -> str:
    strftime = getattr(key, "strftime", None)
    return str(strftime("%Y-%m-%d")) if callable(strftime) else str(key)


def _number(value: Any) -> float | None:
    """NaN, infinity, None and non-numeric values become None: a gap, never a wrong figure."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalise_statement(raw: Mapping[Any, Mapping[Any, Any]] | None) -> Statement:
    """{Timestamp: {item: value}} -> {"YYYY-MM-DD": {item: float | None}}, safe for JSON."""
    if not raw:
        return {}
    return {
        _period_label(period): {str(item): _number(value) for item, value in (items or {}).items()}
        for period, items in raw.items()
    }


class BFinanceFundamentalsProvider:
    """`FundamentalsProvider` over bfinance's yearly statements.

    bfinance serves the statements published up to today and cannot be asked for a past date, so
    this adapter ignores `as_of` beyond validating it. The service never calls it for past dates.
    """

    name = "bfinance"

    def __init__(self, *, ticker_factory: Callable[[str], Any] = _default_ticker) -> None:
        self._factory = ticker_factory

    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> Statements:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        t = self._factory(ticker)  # bfinance accepts the .NS suffix itself
        statements = Statements(
            income_statement=normalise_statement(t.get_income_stmt(as_dict=True, freq="yearly")),
            balance_sheet=normalise_statement(t.get_balance_sheet(as_dict=True, freq="yearly")),
            cash_flow=normalise_statement(t.get_cash_flow(as_dict=True, freq="yearly")),
        )
        if statements.is_empty():
            raise FundamentalsUnavailableError(f"bfinance returned no statements for {ticker}")
        return statements
