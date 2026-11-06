"""Technical indicators computed from daily price bars, using the stockstats library.

stockstats is what upstream TradingAgents uses, so values stay comparable. Its conventions: EMA and
the smoothed averages (RSI, ATR) use `adjust=True` weights, Bollinger bands use the sample standard
deviation, and VWMA weights the typical price (high + low + close) / 3 by volume.

stockstats fills the start of a series with partial-window values. This module replaces those
warm-up values with `None`, so an undefined indicator is never shown as a number. EMA and MACD have
no warm-up gap and are returned from the first bar.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from ta_in.data.point_in_time import guard_records
from ta_in.data.price_cache import PriceBar

# kind -> default window (None: window is required, "fixed": window not allowed, MACD is 12/26/9)
_DEFAULT_WINDOW: dict[str, int | None | str] = {
    "sma": None,
    "ema": None,
    "rsi": 14,
    "macd": "fixed",
    "macd_signal": "fixed",
    "macd_hist": "fixed",
    "boll_mid": 20,
    "boll_upper": 20,
    "boll_lower": 20,
    "atr": 14,
    "vwma": 20,
}

INDICATOR_KINDS: tuple[str, ...] = tuple(_DEFAULT_WINDOW)


class IndicatorValue(BaseModel):
    """One indicator reading for a bar's date. `value` is None while the indicator is undefined."""

    model_config = ConfigDict(frozen=True)

    date: date
    value: float | None


def _column(kind: str, window: int | None) -> str:
    return {
        "sma": f"close_{window}_sma",
        "ema": f"close_{window}_ema",
        "rsi": f"rsi_{window}",
        "macd": "macd",
        "macd_signal": "macds",
        "macd_hist": "macdh",
        "boll_mid": f"boll_{window}",
        "boll_upper": f"boll_ub_{window}",
        "boll_lower": f"boll_lb_{window}",
        "atr": f"atr_{window}",
        "vwma": f"vwma_{window}",
    }[kind]


def _warm_up(kind: str, window: int | None) -> int:
    """Number of leading bars where the indicator is not yet defined."""
    if window is None or kind in ("ema", "macd", "macd_signal", "macd_hist"):
        return 0
    return window if kind == "rsi" else window - 1


def _resolve_window(kind: str, window: int | None) -> int | None:
    if kind not in _DEFAULT_WINDOW:
        raise ValueError(f"unknown indicator {kind!r}; choose from {', '.join(INDICATOR_KINDS)}")
    default = _DEFAULT_WINDOW[kind]
    if default == "fixed":
        if window is not None:
            raise ValueError(f"{kind} has a fixed window (12/26/9); do not pass a window")
        return None
    if window is None:
        if default is None:
            raise ValueError(f"{kind} needs a window")
        assert isinstance(default, int)
        return default
    if window < 1:
        raise ValueError(f"window must be at least 1, got {window}")
    return window


def compute_indicator(
    bars: Sequence[PriceBar],
    kind: str,
    *,
    as_of: datetime,
    window: int | None = None,
) -> list[IndicatorValue]:
    """Compute `kind` over `bars`, one value per bar, in the same order.

    `bars` must be in strictly ascending date order. A bar dated after `as_of` raises
    `PointInTimeError`; nothing is filtered here, filtering belongs to the price provider.
    """
    resolved = _resolve_window(kind, window)
    checked = guard_records(bars, as_of, lambda b: b.date)
    if any(a.date >= b.date for a, b in zip(checked, checked[1:], strict=False)):
        raise ValueError("bars must be in strictly ascending date order")
    if not checked:
        return []

    import pandas as pd
    from stockstats import StockDataFrame

    frame = pd.DataFrame(
        {
            "open": [b.open for b in checked],
            "high": [b.high for b in checked],
            "low": [b.low for b in checked],
            "close": [b.close for b in checked],
            "volume": [float(b.volume) for b in checked],
        },
        index=pd.DatetimeIndex([b.date for b in checked]),
    )
    sdf = StockDataFrame.retype(frame)
    if kind in ("boll_upper", "boll_lower"):
        sdf[_column("boll_mid", resolved)]  # computing the middle band creates both outer bands
    series = sdf[_column(kind, resolved)]
    skip = _warm_up(kind, resolved)
    out: list[IndicatorValue] = []
    for i, (bar, raw) in enumerate(zip(checked, series, strict=True)):
        number = float(raw)
        out.append(
            IndicatorValue(
                date=bar.date,
                value=None if i < skip or math.isnan(number) else number,
            )
        )
    return out
