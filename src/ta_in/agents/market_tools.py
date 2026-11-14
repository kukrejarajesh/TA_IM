"""Tools the market analyst may call: recent prices and technical indicators.

The tools are bound to one ticker and one `as_of` when the toolkit is built. The model never
passes a date, so it cannot ask for data from the future. Everything a tool shows is remembered in
`seen_values`, which the grounding check uses to verify the figures in the finished report. The
`calculate` tool lets the analyst derive a figure it was not shown; its operands must be verified
values (see `agents/calc.py`) and its result is remembered like any other tool output.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ta_in.agents.calc import evaluate, matches_shown
from ta_in.agents.tools import Tool
from ta_in.data.indicators import INDICATOR_KINDS, compute_indicator
from ta_in.data.point_in_time import guard_records
from ta_in.data.price_cache import PriceBar
from ta_in.data.provider import DataProvider
from ta_in.data.snapshot import PriceSnapshot

MAX_INDICATORS = 8
MAX_HISTORY_DAYS = 60
MAX_INDICATOR_POINTS = 30
MAX_CALCULATIONS = 30


class IndicatorLimitReached(Exception):
    """The analyst already chose the maximum number of distinct indicators."""


class CalculationLimitReached(Exception):
    """The analyst already used the maximum number of calculations."""


class CalculateInput(BaseModel):
    expression: str = Field(
        max_length=200,
        description=(
            "Arithmetic over values you were shown, e.g. '1188.98 - 1141.80' or "
            "'avg(1141.80, 1150.20, 1188.98)'. Allowed: + - * /, parentheses, min, max, avg, abs. "
            "Every number must be a snapshot or tool value, or a small constant (0 to 100, or a "
            "decimal under 10). No commas or units."
        ),
    )


class PriceHistoryInput(BaseModel):
    days: int = Field(default=20, ge=1, le=MAX_HISTORY_DAYS, description="Most recent trading days")


class IndicatorInput(BaseModel):
    kind: str = Field(description=f"One of: {', '.join(INDICATOR_KINDS)}")
    window: int | None = Field(
        default=None, ge=1, description="Window in bars; leave out for the default (or MACD)"
    )
    last: int = Field(
        default=10, ge=1, le=MAX_INDICATOR_POINTS, description="How many latest values to return"
    )


class MarketToolkit:
    """The analyst's tools, plus a record of what they showed and which indicators were used."""

    def __init__(
        self,
        provider: DataProvider,
        ticker: str,
        as_of: datetime,
        snapshot: PriceSnapshot | None = None,
    ) -> None:
        self._provider = provider
        self._ticker = ticker
        self._as_of = as_of
        self._snapshot = snapshot
        self.calculations = 0
        self.seen_values: set[float] = set()
        self.indicators_used: set[tuple[str, int | None]] = set()

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_price_history",
                description=(
                    "Daily open, high, low, close and volume for the most recent trading days "
                    "up to the analysis date."
                ),
                input_model=PriceHistoryInput,
                fn=self._price_history,
            ),
            Tool(
                name="get_indicator",
                description=(
                    f"Latest values of one technical indicator. You may use at most "
                    f"{MAX_INDICATORS} different indicators (a kind and window pair); asking "
                    f"for the same one again is free. Kinds: {', '.join(INDICATOR_KINDS)}."
                ),
                input_model=IndicatorInput,
                fn=self._indicator,
            ),
            Tool(
                name="calculate",
                description=(
                    "Compute a figure you were not shown from values you were shown, for "
                    "example the gap to a moving average, a stop set from the ATR, or the "
                    f"average of recent closes. At most {MAX_CALCULATIONS} calculations. "
                    "Use its result for any derived price level in your report."
                ),
                input_model=CalculateInput,
                fn=self._calculate,
            ),
        ]

    def _bars(self) -> list[PriceBar]:
        bars = self._provider.prices(self._ticker, as_of=self._as_of)
        return guard_records(bars, self._as_of, lambda b: b.date)

    def _price_history(self, args: PriceHistoryInput) -> dict[str, Any]:
        bars = self._bars()[-args.days :]
        rows = []
        for b in bars:
            self.seen_values.update((b.open, b.high, b.low, b.close))
            rows.append(
                {
                    "date": b.date.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
            )
        return {"ticker": self._ticker, "bars": rows}

    def _indicator(self, args: IndicatorInput) -> dict[str, Any]:
        key = (args.kind, args.window)
        if key not in self.indicators_used and len(self.indicators_used) >= MAX_INDICATORS:
            raise IndicatorLimitReached(
                f"you may use at most {MAX_INDICATORS} different indicators and already used "
                f"{len(self.indicators_used)}; reuse one of them"
            )
        series = compute_indicator(self._bars(), args.kind, as_of=self._as_of, window=args.window)
        self.indicators_used.add(key)  # only after it worked, so a typo does not use up a slot
        rows = []
        for point in series[-args.last :]:
            if point.value is not None:
                self.seen_values.add(point.value)
            rows.append({"date": point.date.isoformat(), "value": point.value})
        return {"kind": args.kind, "window": args.window, "values": rows}

    def _is_verified(self, value: float, decimals: int) -> bool:
        """Is `value`, written with `decimals` decimals, a snapshot price or a shown value?"""
        shown = list(self.seen_values)
        if self._snapshot is not None:
            shown += [self._snapshot.last_close, self._snapshot.high_52w, self._snapshot.low_52w]
        return any(matches_shown(v, value, decimals) for v in shown)

    def _calculate(self, args: CalculateInput) -> dict[str, Any]:
        if self.calculations >= MAX_CALCULATIONS:
            raise CalculationLimitReached(
                f"you may use at most {MAX_CALCULATIONS} calculations and already used them"
            )
        result = evaluate(args.expression, self._is_verified)
        self.calculations += 1  # only after it worked, so a refused expression costs nothing
        value = round(result.value, 4)
        # Also the absolute values: a gap computed as "close - average" is negative, but a report
        # says "Rs 47.18 below", and the sign of a rupee gap carries no extra claim.
        self.seen_values.update(
            (value, round(result.value, 2), abs(value), abs(round(result.value, 2)))
        )
        return {"expression": result.formula, "value": value}
