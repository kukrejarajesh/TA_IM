"""Serve fundamentals for current-date runs only; withhold them, with a reason, for past dates."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ta_in.fundamentals.models import FundamentalsData, Withheld
from ta_in.fundamentals.provider import FundamentalsProvider

log = logging.getLogger(__name__)

_PAST_REASON = (
    "Past date: fundamentals sources return today's statements and give no announcement dates, "
    "so serving them for an earlier as_of would leak information from after that date."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FundamentalsService:
    """S1.5. A call is "current-date" when `as_of` is no more than `max_age` before the clock.

    Anything older gets `Withheld` and no provider is called, so statements can never reach a
    backtest. For current-date calls providers are tried in order; if all fail or return nothing
    the result is `Withheld` listing each failure, never an exception. `clock` is injectable so
    the data layer never reads "now" on its own in tests.
    """

    def __init__(
        self,
        providers: list[FundamentalsProvider],
        *,
        max_age: timedelta = timedelta(days=2),
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not providers:
            raise ValueError("FundamentalsService requires at least one provider")
        self._providers = list(providers)
        self._max_age = max_age
        self._clock = clock

    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        ticker = ticker.strip().upper()
        now = self._clock()

        if as_of < now - self._max_age:
            log.info("fundamentals withheld for %s: as_of %s is a past date", ticker, as_of)
            return Withheld(ticker=ticker, as_of=as_of, reason=_PAST_REASON)

        failures: list[str] = []
        for provider in self._providers:
            name = getattr(provider, "name", type(provider).__name__)
            try:
                statements = provider.get_fundamentals(ticker, as_of=as_of)
            except Exception as exc:  # explicit Withheld, not a crash
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                log.warning("fundamentals provider %s failed for %s: %s", name, ticker, exc)
                continue
            if statements.is_empty():
                failures.append(f"{name}: returned no statements")
                continue
            return FundamentalsData(
                ticker=ticker,
                as_of=as_of,
                fetched_at=now,
                income_statement=statements.income_statement,
                balance_sheet=statements.balance_sheet,
                cash_flow=statements.cash_flow,
            )

        return Withheld(
            ticker=ticker,
            as_of=as_of,
            reason="No provider could supply fundamentals. " + "; ".join(failures),
        )
