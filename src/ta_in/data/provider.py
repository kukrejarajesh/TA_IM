"""DataProvider interface: every method takes `as_of` and returns nothing dated after it."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ta_in.data.price_cache import PriceBar


@runtime_checkable
class DataProvider(Protocol):
    """Source of market data. `as_of` is required, keyword-only and timezone-aware.

    Implementations must pass what they return through `guard_records` (or filter first),
    so a later-dated record can never reach an agent. Return types are loose until the
    concrete record types land in S1.2-S1.6.
    """

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]: ...

    def news(self, ticker: str, *, as_of: datetime) -> Sequence[Any]: ...

    def fundamentals(self, ticker: str, *, as_of: datetime) -> Any: ...

    def identity(self, ticker: str, *, as_of: datetime) -> Any: ...
