"""Merge news providers, drop items after `as_of`, deduplicate, and log every gap."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ta_in.data.point_in_time import guard_records, is_visible
from ta_in.news.models import NewsItem
from ta_in.news.provider import NewsProvider

log = logging.getLogger(__name__)

# Query parameters that carry no identity: the same story is syndicated with different tags.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
    }
)

# Receives (event_name, payload). Wire this to the run trace (S0.5) when agents exist.
EventSink = Callable[[str, dict[str, Any]], None]


def canonical_url(url: str) -> str:
    """Normalise a URL for deduplication. Never raises; a malformed URL falls back to lowercase."""
    if not url:
        return ""
    try:
        parts = urlparse(url)
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
        return urlunparse(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                parts.params,
                urlencode(query),
                "",
            )
        )
    except ValueError:
        return url.strip().lower()


def _dedup_key(item: NewsItem) -> tuple[str, ...]:
    cu = canonical_url(item.url)
    if cu:
        return ("url", cu)
    return ("title_time", item.title.strip().lower(), item.published_time.isoformat())


class NewsService:
    """Apply the S1.4 contract across one or more providers.

    Order: collect (a provider failure is a logged gap, never a crash), drop items published
    after `as_of` (each drop logged), deduplicate (earliest copy wins), sort oldest first.
    An empty result is `[]`, logged once. The survivors also go through `guard_records` as a
    last safety net.
    """

    def __init__(
        self, providers: list[NewsProvider], *, event_sink: EventSink | None = None
    ) -> None:
        if not providers:
            raise ValueError("NewsService requires at least one provider")
        self._providers = list(providers)
        self._event_sink = event_sink

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        ticker = ticker.strip().upper()

        raw = self._collect(ticker, as_of)
        kept: list[NewsItem] = []
        dropped = 0
        for item in raw:
            if is_visible(item.published_time, as_of):
                kept.append(item)
            else:
                dropped += 1
                self._log_drop(ticker, as_of, item)

        unique = self._dedup(ticker, as_of, kept)
        unique.sort(key=lambda i: i.published_time)
        unique = guard_records(unique, as_of, lambda i: i.published_time)

        if not unique:
            self._emit(
                "news.empty",
                {
                    "ticker": ticker,
                    "as_of": as_of.isoformat(),
                    "raw": len(raw),
                    "dropped_after_as_of": dropped,
                },
            )
        return unique

    def _collect(self, ticker: str, as_of: datetime) -> list[NewsItem]:
        items: list[NewsItem] = []
        for provider in self._providers:
            name = getattr(provider, "name", type(provider).__name__)
            try:
                items.extend(provider.get_news(ticker, as_of=as_of))
            except Exception as exc:  # explicit gap, not a crash
                self._emit(
                    "news.provider_failed",
                    {
                        "ticker": ticker,
                        "as_of": as_of.isoformat(),
                        "provider": name,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                log.warning("news provider %s failed for %s: %s", name, ticker, exc)
        return items

    def _dedup(self, ticker: str, as_of: datetime, items: list[NewsItem]) -> list[NewsItem]:
        chosen: dict[tuple[str, ...], NewsItem] = {}
        for item in items:
            key = _dedup_key(item)
            prev = chosen.get(key)
            # The first outlet to carry a story is the one a decision maker could have seen.
            if prev is None or item.published_time < prev.published_time:
                chosen[key] = item
        removed = len(items) - len(chosen)
        if removed:
            self._emit(
                "news.deduped",
                {
                    "ticker": ticker,
                    "as_of": as_of.isoformat(),
                    "removed": removed,
                    "kept": len(chosen),
                    "sources": dict(Counter(i.source for i in items)),
                },
            )
        return list(chosen.values())

    def _log_drop(self, ticker: str, as_of: datetime, item: NewsItem) -> None:
        self._emit(
            "news.dropped_after_as_of",
            {
                "ticker": ticker,
                "as_of": as_of.isoformat(),
                "published_time": item.published_time.isoformat(),
                "source": item.source,
                "title": item.title[:120],
                "url": item.url,
            },
        )
        log.info(
            "news dropped after as_of ticker=%s as_of=%s published=%s source=%s",
            ticker,
            as_of.isoformat(),
            item.published_time.isoformat(),
            item.source,
        )

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(name, payload)
        except Exception:  # tracing must never break a run
            log.exception("news event sink failed for %s", name)
