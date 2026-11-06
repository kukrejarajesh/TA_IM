"""S1.7: verified price snapshot (last close, 52-week high and low, recent returns)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from ta_in.data import (
    IST,
    PointInTimeError,
    PriceBar,
    PriceProvider,
    PriceSnapshot,
    build_snapshot,
    render_snapshot,
)

TICKER = "RELIANCE.NS"


def _bar(d: date, close: float) -> PriceBar:
    return PriceBar(
        date=d,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        adj_close=close,
        volume=1_000,
    )


def _weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series_a(n: int = 70) -> list[PriceBar]:
    """n weekdays from Mon 2026-01-05, close = 100 + i. 70 bars end on Fri 2026-04-10."""
    return [_bar(d, 100.0 + i) for i, d in enumerate(_weekdays(date(2026, 1, 5), n))]


def _at(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=IST)


A_LAST = date(2026, 4, 10)
A_FIRST = date(2026, 1, 5)


def test_last_close_and_returns_match_the_series_exactly() -> None:
    bars = _series_a()
    snap = build_snapshot(bars, as_of=_at(A_LAST))
    assert snap.as_of_date == A_LAST
    assert snap.last_close == 169.0 == bars[-1].close
    # close[-1] is 169; the closes n bars earlier are 168, 164, 149 and 109
    assert snap.return_1d == pytest.approx(169 / 168 - 1, abs=1e-12)
    assert snap.return_5d == pytest.approx(169 / 164 - 1, abs=1e-12)
    assert snap.return_20d == pytest.approx(169 / 149 - 1, abs=1e-12)
    assert snap.return_60d == pytest.approx(169 / 109 - 1, abs=1e-12)


def _series_b() -> tuple[list[PriceBar], date]:
    """Weekly Friday bars from 2025-01-03 (k = 0..79). Closes are 120 except four marked points."""
    dates = [date(2025, 1, 3) + timedelta(days=7 * k) for k in range(80)]
    close = dict.fromkeys(range(80), 120.0)
    close[5] = 200.0  # old peak, outside the 52-week window
    close[10] = 50.0  # old trough, outside the window
    close[50] = 180.0  # peak inside the window
    close[60] = 90.0  # trough inside the window
    return [_bar(d, close[k]) for k, d in enumerate(dates)], dates[-1]


def test_52_week_range_uses_closes_inside_the_window_only() -> None:
    bars, last = _series_b()
    assert last == date(2026, 7, 10)
    snap = build_snapshot(bars, as_of=_at(last))
    assert snap.high_52w == 180.0
    assert snap.high_52w_date == date(2025, 1, 3) + timedelta(days=7 * 50)
    assert snap.low_52w == 90.0
    assert snap.low_52w_date == date(2025, 1, 3) + timedelta(days=7 * 60)
    assert snap.history_complete_52w is True


def test_window_boundary_is_365_days_inclusive() -> None:
    last = date(2026, 6, 30)
    inside = last - timedelta(days=365)
    outside = inside - timedelta(days=1)
    bars = [_bar(outside, 500.0), _bar(inside, 300.0), _bar(last, 200.0)]
    snap = build_snapshot(bars, as_of=_at(last))
    assert snap.high_52w == 300.0  # the 365-day-old bar counts
    assert snap.high_52w_date == inside
    assert snap.history_complete_52w is True  # first bar is older than the window start


def test_ties_pick_the_earliest_date() -> None:
    d = _weekdays(date(2026, 3, 2), 3)
    snap = build_snapshot([_bar(d[0], 10.0), _bar(d[1], 10.0), _bar(d[2], 10.0)], as_of=_at(d[2]))
    assert snap.high_52w_date == d[0]
    assert snap.low_52w_date == d[0]


def test_short_history_gives_none_returns_and_a_partial_flag() -> None:
    bars = _series_a(10)
    snap = build_snapshot(bars, as_of=_at(bars[-1].date))
    assert snap.return_1d is not None
    assert snap.return_5d is not None
    assert snap.return_20d is None
    assert snap.return_60d is None
    assert snap.history_complete_52w is False
    assert snap.high_52w == 109.0
    assert snap.low_52w == 100.0


def test_a_bar_after_as_of_raises() -> None:
    with pytest.raises(PointInTimeError):
        build_snapshot(_series_a(), as_of=_at(date(2026, 4, 9)))


def test_bad_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_snapshot(_series_a(), as_of=datetime(2026, 6, 30, 16, 0))
    with pytest.raises(ValueError, match="no bars"):
        build_snapshot([], as_of=_at(A_LAST))
    bars = _series_a(5)
    with pytest.raises(ValueError, match="ascending"):
        build_snapshot(list(reversed(bars)), as_of=_at(A_LAST))
    with pytest.raises(ValueError, match="ascending"):
        build_snapshot([bars[0], bars[0]], as_of=_at(A_LAST))


def test_snapshot_is_frozen() -> None:
    snap = build_snapshot(_series_a(), as_of=_at(A_LAST))
    with pytest.raises(ValueError):
        snap.last_close = 1.0  # type: ignore[misc]


GOLDEN_A = """\
VERIFIED PRICE SNAPSHOT: RELIANCE.NS (last bar 2026-04-10)
Last close: 169.00
52-week high (close): 169.00 on 2026-04-10
52-week low (close): 100.00 on 2026-01-05
Return 1d: +0.60%
Return 5d: +3.05%
Return 20d: +13.42%
Return 60d: +55.05%
Note: partial 52-week range, fewer than 365 days of history are available."""


def test_render_matches_the_golden_text() -> None:
    snap = build_snapshot(_series_a(), as_of=_at(A_LAST))
    assert render_snapshot(snap, TICKER) == GOLDEN_A
    assert render_snapshot(snap, TICKER) == render_snapshot(snap, TICKER)


def test_render_marks_missing_returns_and_omits_the_note_for_a_full_year() -> None:
    short = build_snapshot(_series_a(10), as_of=_at(_series_a(10)[-1].date))
    text = render_snapshot(short, TICKER)
    assert "Return 20d: n/a (insufficient history)" in text
    assert "Return 60d: n/a (insufficient history)" in text

    bars, last = _series_b()
    full = render_snapshot(build_snapshot(bars, as_of=_at(last)), TICKER)
    assert "partial" not in full
    assert "52-week high (close): 180.00" in full
    assert "52-week low (close): 90.00" in full


def test_render_contains_every_figure_of_the_snapshot() -> None:
    snap = build_snapshot(_series_a(), as_of=_at(A_LAST))
    text = render_snapshot(snap, TICKER)
    for figure in (
        f"{snap.last_close:.2f}",
        f"{snap.high_52w:.2f}",
        f"{snap.low_52w:.2f}",
        snap.high_52w_date.isoformat(),
        snap.low_52w_date.isoformat(),
    ):
        assert figure in text
    assert isinstance(snap, PriceSnapshot)


def test_snapshot_through_the_provider_ignores_bars_after_as_of(tmp_path: Path) -> None:
    all_bars = _series_a(75)  # runs past 2026-04-10; a careless vendor returns all of it

    def fetcher(_ticker: str, start: date, _end: date) -> list[PriceBar]:
        return [b for b in all_bars if b.date >= start]

    provider = PriceProvider(fetcher, tmp_path, lookback_days=120)
    bars = provider.prices(TICKER, as_of=_at(A_LAST))
    snap = build_snapshot(bars, as_of=_at(A_LAST))
    assert snap.as_of_date == A_LAST
    assert snap.last_close == 169.0
    assert snap == build_snapshot(_series_a(), as_of=_at(A_LAST))
