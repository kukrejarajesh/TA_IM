"""The contract a news source implements. One adapter per source; the service merges them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ta_in.news.models import NewsItem


@runtime_checkable
class NewsProvider(Protocol):
    """A single news source.

    `as_of` is keyword-only and timezone-aware. An adapter may bound its query with it, but
    `NewsService` is the authoritative filter. An adapter must RAISE when its source fails, so
    an outage is recorded as a gap and never looks like "no news".
    """

    name: str

    def get_news(self, ticker: str, *, as_of: datetime) -> Sequence[NewsItem]: ...
