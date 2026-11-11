"""Currents API adapter: narrative market news. API errors raise so the service logs a gap."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ta_in.news.models import NewsItem

log = logging.getLogger(__name__)

_BASE_URL = "https://api.currentsapi.services/v1/search"


def _default_get(
    url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float
) -> Any:
    import requests

    return requests.get(url, params=params, headers=headers, timeout=timeout)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# Currents refuses a start_date older than this many days before the request is made
# ("date_range: Max lookback is 30 days", seen on the plan in use on 2026-11-11).
MAX_HISTORY_DAYS = 30
# Safety margin so a start_date exactly on the limit is not rejected by clock skew.
_EDGE = timedelta(minutes=5)
# One request may span at most this many days ("date_range: Max range is 7 days").
MAX_RANGE_DAYS = 7

_COMPANY_SUFFIXES = {"limited", "ltd", "ltd.", "inc", "inc.", "corporation", "corp", "corp."}
_FINANCE_TERMS = "stock OR shares OR earnings OR profit OR revenue OR NSE OR BSE"


def clean_company_name(name: str) -> str:
    """'Infosys Limited' -> 'Infosys'. Drops legal suffixes and quote characters."""
    words = name.replace('"', " ").split()
    while len(words) > 1 and words[-1].lower() in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def build_query(symbol: str, company_name: str | None) -> str:
    """Boolean search: company name and/or symbol, AND at least one finance term.

    Articles name companies ("Infosys"), not exchange symbols ("INFY"), so the name is searched
    as well as the symbol. Terms are quoted, which keeps "RELIANCE" from matching "reliance on".
    """
    terms = [symbol.replace('"', "")]
    if company_name:
        cleaned = clean_company_name(company_name)
        if cleaned and cleaned.lower() not in {t.lower() for t in terms}:
            terms.insert(0, cleaned)
    quoted = " OR ".join(f'"{t}"' for t in terms)
    head = f"({quoted})" if len(terms) > 1 else quoted
    return f"{head} AND ({_FINANCE_TERMS})"


class CurrentsNewsProvider:
    """`NewsProvider` over the Currents search endpoint.

    `http_get` and `clock` are injectable for tests. Currents only serves the last
    `max_history_days` days, measured from the moment of the request, so an `as_of` older than
    that cannot be answered: the adapter raises a clear error (a logged gap) instead of sending a
    request that is bound to fail. A window that only partly reaches back is clamped.
    """

    name = "Currents"

    def __init__(
        self,
        api_key: str,
        *,
        lookback_days: int = 7,
        max_history_days: int = MAX_HISTORY_DAYS,
        timeout: float = 10.0,
        http_get: Callable[..., Any] = _default_get,
        clock: Callable[[], datetime] = _utc_now,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("a Currents API key is required (CURRENTS_API_KEY)")
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if lookback_days > MAX_RANGE_DAYS:
            raise ValueError(
                f"Currents allows at most {MAX_RANGE_DAYS} days per request, "
                f"got lookback_days={lookback_days}"
            )
        self._name_resolver = name_resolver
        if max_history_days <= 0:
            raise ValueError("max_history_days must be positive")
        self._api_key = api_key
        self._lookback_days = lookback_days
        self._max_history = timedelta(days=max_history_days)
        self._timeout = timeout
        self._get = http_get
        self._clock = clock

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        symbol = ticker[:-3] if ticker.upper().endswith(".NS") else ticker
        end = as_of.astimezone(UTC)
        earliest = self._clock() - self._max_history + _EDGE
        if end < earliest:
            raise RuntimeError(
                f"Currents only serves the last {self._max_history.days} days; as_of "
                f"{as_of.isoformat()} is older than that, so no news can be fetched for it"
            )
        start = max(end - timedelta(days=self._lookback_days), earliest)
        params = {
            "query": build_query(symbol, self._company_name(ticker)),
            "country": "IN",
            "category": "business",
            "language": "en",
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": 20,
        }
        response = self._get(
            _BASE_URL,
            params=params,
            headers={"Authorization": self._api_key},
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            # Keep Currents' own explanation: a bare "400" does not say which parameter it rejected.
            detail = str(getattr(response, "text", "") or "").strip()[:300]
            raise RuntimeError(
                f"Currents HTTP error: {exc}. Response body: {detail or '(empty)'}"
            ) from exc
        data = response.json()
        if data.get("status") != "ok":
            # Do not echo the payload: it can contain request details.
            raise RuntimeError(f"Currents returned status {data.get('status')!r}")

        items: list[NewsItem] = []
        for article in data.get("news", []):
            try:
                items.append(self._to_item(article))
            except (KeyError, ValueError, TypeError) as exc:
                log.warning("Currents article skipped for %s: %s", symbol, exc)
        return items

    def _company_name(self, ticker: str) -> str | None:
        """Best-effort name for the query; a failing resolver falls back to the symbol alone."""
        if self._name_resolver is None:
            return None
        try:
            return self._name_resolver(ticker)
        except Exception as exc:
            log.warning("company name lookup failed for %s: %s", ticker, exc)
            return None

    def _to_item(self, article: dict[str, Any]) -> NewsItem:
        title = (article.get("title") or "").strip()
        url = (article.get("url") or "").strip()
        if not title:
            raise ValueError("no title")
        if not url:
            raise ValueError("no url")
        published = datetime.fromisoformat(article["published"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        return NewsItem(
            title=title,
            source=article.get("author") or self.name,
            published_time=published,
            url=url,
            summary=(article.get("description") or "").strip() or None,
        )
