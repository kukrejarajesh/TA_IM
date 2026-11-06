"""Verified price snapshot: figures computed from the price series, for grounding agent reports.

Everything is derived from closing prices (`close`, not `adj_close`). Nothing is invented: a return
without enough history is `None`, and a 52-week range built on less than a year of bars is flagged.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from ta_in.data.point_in_time import guard_records
from ta_in.data.price_cache import PriceBar

WINDOW_DAYS = 365
# Return horizons in trading bars; 20 matches the default holding period in feature_list.json.
RETURN_BARS = (1, 5, 20, 60)


class PriceSnapshot(BaseModel):
    """Figures an agent may quote about the price. `as_of_date` is the last bar's date."""

    model_config = ConfigDict(frozen=True)

    as_of_date: date
    last_close: float
    high_52w: float
    high_52w_date: date
    low_52w: float
    low_52w_date: date
    history_complete_52w: bool
    return_1d: float | None
    return_5d: float | None
    return_20d: float | None
    return_60d: float | None


def build_snapshot(bars: Sequence[PriceBar], *, as_of: datetime) -> PriceSnapshot:
    """Build the snapshot from `bars`, which must be ascending by date and none later than `as_of`.

    A later bar raises `PointInTimeError`; nothing is filtered here, filtering belongs to the
    price provider.
    """
    checked = guard_records(bars, as_of, lambda b: b.date)
    if not checked:
        raise ValueError("no bars to build a snapshot from")
    if any(a.date >= b.date for a, b in zip(checked, checked[1:], strict=False)):
        raise ValueError("bars must be in strictly ascending date order")

    last = checked[-1]
    window_start = last.date - timedelta(days=WINDOW_DAYS)
    window = [b for b in checked if b.date >= window_start]

    high = window[0]
    low = window[0]
    for b in window[1:]:
        if b.close > high.close:  # strict, so the earliest date wins a tie
            high = b
        if b.close < low.close:
            low = b

    closes = [b.close for b in checked]
    returns: dict[int, float | None] = {
        n: (closes[-1] / closes[-1 - n] - 1 if len(closes) > n else None) for n in RETURN_BARS
    }
    return PriceSnapshot(
        as_of_date=last.date,
        last_close=last.close,
        high_52w=high.close,
        high_52w_date=high.date,
        low_52w=low.close,
        low_52w_date=low.date,
        history_complete_52w=checked[0].date <= window_start,
        return_1d=returns[1],
        return_5d=returns[5],
        return_20d=returns[20],
        return_60d=returns[60],
    )


def _pct(value: float | None) -> str:
    return "n/a (insufficient history)" if value is None else f"{value * 100:+.2f}%"


def render_snapshot(snapshot: PriceSnapshot, ticker: str) -> str:
    """Fixed-format text block for an agent's context. Same snapshot, same text, always."""
    lines = [
        f"VERIFIED PRICE SNAPSHOT: {ticker} (last bar {snapshot.as_of_date.isoformat()})",
        f"Last close: {snapshot.last_close:.2f}",
        f"52-week high (close): {snapshot.high_52w:.2f} on {snapshot.high_52w_date.isoformat()}",
        f"52-week low (close): {snapshot.low_52w:.2f} on {snapshot.low_52w_date.isoformat()}",
        f"Return 1d: {_pct(snapshot.return_1d)}",
        f"Return 5d: {_pct(snapshot.return_5d)}",
        f"Return 20d: {_pct(snapshot.return_20d)}",
        f"Return 60d: {_pct(snapshot.return_60d)}",
    ]
    if not snapshot.history_complete_52w:
        lines.append(
            f"Note: partial 52-week range, fewer than {WINDOW_DAYS} days of history are available."
        )
    return "\n".join(lines)
