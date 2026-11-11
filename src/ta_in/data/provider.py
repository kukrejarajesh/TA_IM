"""DataProvider interface: every method takes `as_of` and returns nothing dated after it."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ta_in.data.identity import CompanyIdentity
from ta_in.data.price_cache import PriceBar

if TYPE_CHECKING:  # news imports the point-in-time guard from this package; avoid a cycle
    from ta_in.fundamentals.models import FundamentalsData, Withheld
    from ta_in.news.models import NewsItem


@runtime_checkable
class DataProvider(Protocol):
    """Source of market data. `as_of` is required, keyword-only and timezone-aware.

    Implementations must pass what they return through `guard_records` (or filter first),
    so a later-dated record can never reach an agent. Return types are loose until the
    concrete record types land in S1.2-S1.6.
    """

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]: ...

    def news(self, ticker: str, *, as_of: datetime) -> Sequence[NewsItem]: ...

    def fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld: ...

    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity: ...
