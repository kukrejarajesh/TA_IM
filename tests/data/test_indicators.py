"""S1.3: indicators computed from price bars, checked against hand-calculated values.

All expected series below were worked out with plain arithmetic (see the comments), not copied from
the library's output, for the 12-bar series in `_bars()` and a window of 3. The library (stockstats)
defines the conventions, so each comment states them: EMA and the smoothed averages use
`adjust=True` weights, Bollinger bands use the sample standard deviation, and VWMA weights the
typical price (high + low + close) / 3 by volume.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from ta_in.data import (
    INDICATOR_KINDS,
    IST,
    IndicatorValue,
    PointInTimeError,
    PriceBar,
    compute_indicator,
)

AS_OF = datetime(2026, 2, 27, 16, 0, tzinfo=IST)
LATE_AS_OF = datetime(2026, 6, 30, 16, 0, tzinfo=IST)  # for the longer MACD series

CLOSE = [10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18]
UP = [2, 1, 3, 1, 2, 3, 1, 2, 1, 3, 2, 1]  # high = close + UP
DOWN = [1, 2, 1, 1, 3, 1, 2, 1, 2, 1, 1, 2]  # low = close - DOWN
VOLUME = [100, 120, 90, 150, 110, 130, 95, 140, 105, 160, 125, 115]
START = date(2026, 2, 2)


def _days(n: int) -> list[date]:
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bars() -> list[PriceBar]:
    return [
        PriceBar(
            date=d,
            open=c - 0.5,
            high=c + u,
            low=c - dn,
            close=c,
            adj_close=c,
            volume=v,
        )
        for d, c, u, dn, v in zip(_days(len(CLOSE)), CLOSE, UP, DOWN, VOLUME, strict=True)
    ]


def _values(kind: str, window: int | None = 3) -> list[float | None]:
    out = compute_indicator(_bars(), kind, as_of=AS_OF, window=window)
    return [v.value for v in out]


def _assert_series(got: list[float | None], want: list[float | None]) -> None:
    assert len(got) == len(want)
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        if w is None:
            assert g is None, f"index {i}: expected None, got {g}"
        else:
            assert g is not None, f"index {i}: expected {w}, got None"
            assert g == pytest.approx(w, abs=1e-5), f"index {i}"


def test_sma_matches_hand_calculation() -> None:
    # index 2: (10 + 11 + 12) / 3 = 11;  index 3: (11 + 12 + 11) / 3 = 11.3333
    want = [None, None, 11.0, 11.333333, 12.0, 12.666667, 13.333333, 14.0, 14.666667, 15.333333]
    _assert_series(_values("sma"), [*want, 16.0, 16.666667])


def test_ema_matches_hand_calculation() -> None:
    # alpha = 2 / (3 + 1) = 0.5, weights 1, .5, .25, ... newest first (adjust=True).
    # index 1: (11 + .5 * 10) / 1.5 = 10.6667
    # index 3: (11 + .5 * 12 + .25 * 11 + .125 * 10) / 1.875 = 21 / 1.875 = 11.2
    want = [10.0, 10.666667, 11.428571, 11.2, 12.129032, 13.079365, 13.03937, 14.023529]
    _assert_series(_values("ema"), [*want, 15.013699, 15.006843, 16.003908, 17.002198])


def test_bollinger_bands_match_hand_calculation() -> None:
    # index 3: window 11, 12, 11 -> mean 11.3333; deviations -1/3, 2/3, -1/3;
    # sample variance (1/9 + 4/9 + 1/9) / 2 = 1/3; std 0.57735
    # upper = 11.3333 + 2 * 0.57735 = 12.4880;  lower = 11.3333 - 1.1547 = 10.1786
    mid = [None, None, 11.0, 11.333333, 12.0, 12.666667, 13.333333, 14.0]
    _assert_series(_values("boll_mid"), [*mid, 14.666667, 15.333333, 16.0, 16.666667])
    upper = [None, None, 13.0, 12.488034, 14.0, 15.721717, 14.488034, 16.0]
    _assert_series(_values("boll_upper"), [*upper, 17.721717, 16.488034, 18.0, 19.721717])
    lower = [None, None, 9.0, 10.178633, 10.0, 9.611616, 12.178633, 12.0]
    _assert_series(_values("boll_lower"), [*lower, 11.611616, 14.178633, 14.0, 13.611616])


def test_vwma_matches_hand_calculation() -> None:
    # typical price = (close + high + low) / 3, weighted by volume over the window.
    # index 3: tp = 10.6667, 12.6667, 11 with volume 120, 90, 150
    # (120 * 10.6667 + 90 * 12.6667 + 150 * 11) / 360 = 4070 / 360 = 11.3056
    want = [None, None, 11.139785, 11.305556, 11.952381, 12.692308, 13.442786, 14.401826]
    _assert_series(_values("vwma"), [*want, 14.691176, 15.55144, 16.200855, 16.7625])


def test_atr_matches_hand_calculation() -> None:
    # true range = max(high - low, |high - prev close|, |low - prev close|); first bar high - low.
    # TR: 3, 3, 4, 2, ... smoothed with alpha = 1/3 (adjust=True weights 1, 2/3, 4/9, ...).
    # index 2: (4 + 3 * 2/3 + 3 * 4/9) / (1 + 2/3 + 4/9) = 7.3333 / 2.1111 = 3.4737
    want = [None, None, 3.473684, 2.861538, 3.682464, 3.798496, 3.515784, 3.683743]
    _assert_series(_values("atr"), [*want, 3.449742, 3.636398, 3.759016, 3.504046])


def test_rsi_matches_hand_calculation() -> None:
    # gains and losses smoothed with alpha = 1/3 (adjust=True), RSI = 100 * up / (up + down).
    # index 3: price changes 0, +1, +1, -1 -> up weights 0, 1*2/3, 1*4/9, 0 = 1.1111; down weight 1
    # RSI = 100 * 1.1111 / (1.1111 + 1) = 52.6316; undefined for the first `window` bars.
    want = [None, None, None, 52.631579, 80.434783, 86.415094, 59.249677, 79.027963]
    _assert_series(_values("rsi"), [*want, 84.624771, 60.433097, 78.700099, 84.178409])


def test_macd_is_flat_for_a_constant_series() -> None:
    bars = [
        PriceBar(date=d, open=50, high=50, low=50, close=50, adj_close=50, volume=1)
        for d in _days(40)
    ]
    for kind in ("macd", "macd_signal", "macd_hist"):
        out = compute_indicator(bars, kind, as_of=LATE_AS_OF, window=None)
        assert all(v.value == pytest.approx(0.0, abs=1e-12) for v in out), kind


def test_macd_is_fast_ema_minus_slow_ema() -> None:
    bars = [
        PriceBar(date=d, open=c, high=c + 1, low=c - 1, close=c, adj_close=c, volume=10)
        for d, c in zip(_days(45), (100 + (i * 7) % 11 + i * 0.5 for i in range(45)), strict=True)
    ]
    fast = compute_indicator(bars, "ema", as_of=LATE_AS_OF, window=12)
    slow = compute_indicator(bars, "ema", as_of=LATE_AS_OF, window=26)
    macd = compute_indicator(bars, "macd", as_of=LATE_AS_OF, window=None)
    signal = compute_indicator(bars, "macd_signal", as_of=LATE_AS_OF, window=None)
    hist = compute_indicator(bars, "macd_hist", as_of=LATE_AS_OF, window=None)
    for f, s, m, g, h in zip(fast, slow, macd, signal, hist, strict=True):
        assert f.value is not None and s.value is not None
        assert m.value == pytest.approx(f.value - s.value, abs=1e-9)
        assert m.value is not None and g.value is not None
        assert h.value == pytest.approx(m.value - g.value, abs=1e-9)


def test_output_has_one_value_per_bar_in_order() -> None:
    bars = _bars()
    out = compute_indicator(bars, "sma", as_of=AS_OF, window=3)
    assert [v.date for v in out] == [b.date for b in bars]
    assert all(isinstance(v, IndicatorValue) for v in out)


def test_warm_up_days_are_none_not_invented() -> None:
    assert _values("sma", 5)[:4] == [None] * 4
    assert _values("sma", 5)[4] is not None
    assert _values("rsi", 5)[:5] == [None] * 5
    assert _values("rsi", 5)[5] is not None


def test_default_windows_apply() -> None:
    # Bollinger defaults to 20 bars, so a 12-bar series is all warm-up.
    assert _values("boll_mid", None) == [None] * 12
    assert _values("atr", None) == [None] * 12  # default window 14


def test_bar_after_as_of_raises() -> None:
    bars = _bars()
    with pytest.raises(PointInTimeError):
        compute_indicator(bars, "sma", as_of=datetime(2026, 2, 10, 16, 0, tzinfo=IST), window=3)


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_indicator(_bars(), "sma", as_of=datetime(2026, 2, 27, 16, 0), window=3)


def test_bad_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        compute_indicator(_bars(), "nope", as_of=AS_OF, window=3)
    with pytest.raises(ValueError, match="window"):
        compute_indicator(_bars(), "sma", as_of=AS_OF, window=0)
    with pytest.raises(ValueError, match="window"):
        compute_indicator(_bars(), "sma", as_of=AS_OF, window=None)  # sma has no default
    with pytest.raises(ValueError, match="window"):
        compute_indicator(_bars(), "macd", as_of=AS_OF, window=5)  # macd is fixed at 12/26/9
    bars = _bars()
    with pytest.raises(ValueError, match="ascending"):
        compute_indicator(list(reversed(bars)), "sma", as_of=AS_OF, window=3)
    with pytest.raises(ValueError, match="ascending"):
        compute_indicator([bars[0], bars[0]], "sma", as_of=AS_OF, window=3)


def test_empty_input_gives_empty_list() -> None:
    assert compute_indicator([], "sma", as_of=AS_OF, window=3) == []


def test_all_documented_kinds_exist() -> None:
    assert set(INDICATOR_KINDS) == {
        "sma",
        "ema",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "boll_mid",
        "boll_upper",
        "boll_lower",
        "atr",
        "vwma",
    }
