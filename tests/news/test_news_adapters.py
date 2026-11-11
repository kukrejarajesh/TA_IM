"""Currents adapter: canned payloads offline; failures raise, never look like no news."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ta_in.data import IST
from ta_in.news.adapters.currents import (
    CurrentsNewsProvider,
    build_query,
    clean_company_name,
)

AS_OF = datetime(2025, 6, 30, 16, 0, tzinfo=IST)
NOW = datetime(2025, 7, 1, 9, 0, tzinfo=UTC)  # fixed clock: AS_OF is "yesterday"


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    @property
    def text(self) -> str:
        return "invalid parameter: start_date"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _currents(
    payload: Any, status: int = 200, *, now: datetime = NOW
) -> tuple[CurrentsNewsProvider, list[dict]]:
    seen: list[dict] = []

    def get(url: str, *, params: dict, headers: dict, timeout: float) -> FakeResponse:
        seen.append({"url": url, "params": params, "headers": headers})
        return FakeResponse(payload, status)

    return CurrentsNewsProvider("test-key", http_get=get, clock=lambda: now), seen


def test_currents_as_of_older_than_30_days_raises_a_clear_error_without_calling_the_api() -> None:
    provider, seen = _currents({"status": "ok", "news": []}, now=datetime(2025, 8, 15, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="only serves the last 30 days"):
        provider.get_news("RELIANCE.NS", as_of=AS_OF)
    assert seen == []


def test_currents_as_of_inside_30_days_is_allowed_at_the_edge() -> None:
    now = datetime(2025, 7, 29, 12, 0, tzinfo=UTC)  # AS_OF is 29.1 days old
    provider, seen = _currents({"status": "ok", "news": []}, now=now)
    provider.get_news("RELIANCE.NS", as_of=AS_OF)
    assert len(seen) == 1


def test_currents_window_reaching_past_the_limit_is_clamped() -> None:
    now = datetime(2025, 7, 29, 12, 0, tzinfo=UTC)  # 7-day window would start 36 days back
    provider, seen = _currents({"status": "ok", "news": []}, now=now)
    provider.get_news("RELIANCE.NS", as_of=AS_OF)
    start = datetime.strptime(seen[0]["params"]["start_date"], "%Y-%m-%dT%H:%M:%SZ")
    assert start.replace(tzinfo=UTC) >= now - timedelta(days=30)


def test_currents_maps_articles_to_news_items() -> None:
    payload = {
        "status": "ok",
        "news": [
            {
                "title": " Reliance shares rise ",
                "url": "https://news.test/a",
                "published": "2025-06-30 04:00:00 +0000",
                "author": "Reuters",
                "description": "Up 2%",
            }
        ],
    }
    provider, _ = _currents(payload)
    (item,) = provider.get_news("RELIANCE.NS", as_of=AS_OF)
    assert item.title == "Reliance shares rise"
    assert item.source == "Reuters"
    assert item.summary == "Up 2%"
    assert item.published_time == datetime(2025, 6, 30, 4, 0, tzinfo=UTC)


def test_currents_query_uses_bare_symbol_and_as_of_in_utc() -> None:
    provider, seen = _currents({"status": "ok", "news": []})
    provider.get_news("RELIANCE.NS", as_of=AS_OF)
    params = seen[0]["params"]
    assert "RELIANCE" in params["query"]
    assert ".NS" not in params["query"]
    assert params["end_date"] == "2025-06-30T10:30:00Z"  # 16:00 IST
    assert seen[0]["headers"] == {"Authorization": "test-key"}


def test_currents_skips_malformed_articles_but_keeps_good_ones() -> None:
    payload = {
        "status": "ok",
        "news": [
            {"title": "", "url": "https://news.test/x", "published": "2025-06-30T01:00:00Z"},
            {"title": "t", "url": "", "published": "2025-06-30T01:00:00Z"},
            {"title": "good", "url": "https://news.test/g", "published": "2025-06-30T01:00:00Z"},
        ],
    }
    provider, _ = _currents(payload)
    assert [i.title for i in provider.get_news("TCS.NS", as_of=AS_OF)] == ["good"]


def test_currents_http_error_raises_so_the_service_records_a_gap() -> None:
    provider, _ = _currents({}, status=429)
    with pytest.raises(RuntimeError):
        provider.get_news("TCS.NS", as_of=AS_OF)


def test_clean_company_name_drops_legal_suffixes() -> None:
    assert clean_company_name("Infosys Limited") == "Infosys"
    assert clean_company_name("Tata Consultancy Services Ltd.") == "Tata Consultancy Services"
    assert clean_company_name("Limited") == "Limited"  # never empty the name


def test_query_uses_name_and_symbol_when_a_name_is_known() -> None:
    q = build_query("INFY", "Infosys Limited")
    assert q.startswith('("Infosys" OR "INFY") AND (')
    assert "stock OR shares" in q


def test_query_falls_back_to_the_symbol_alone() -> None:
    assert build_query("RELIANCE", None).startswith('"RELIANCE" AND (')
    # name equal to the symbol is not repeated
    assert build_query("RELIANCE", "Reliance").startswith('"RELIANCE" AND (')


def test_currents_sends_the_company_name_from_the_resolver() -> None:
    seen: list[dict] = []

    def get(url: str, *, params: dict, headers: dict, timeout: float) -> FakeResponse:
        seen.append(params)
        return FakeResponse({"status": "ok", "news": []})

    provider = CurrentsNewsProvider(
        "k", http_get=get, clock=lambda: NOW, name_resolver=lambda t: "Infosys Limited"
    )
    provider.get_news("INFY.NS", as_of=AS_OF)
    assert '"Infosys" OR "INFY"' in seen[0]["query"]


def test_a_failing_name_resolver_falls_back_to_the_symbol() -> None:
    def boom(ticker: str) -> str | None:
        raise RuntimeError("kite down")

    seen: list[dict] = []

    def get(url: str, *, params: dict, headers: dict, timeout: float) -> FakeResponse:
        seen.append(params)
        return FakeResponse({"status": "ok", "news": []})

    provider = CurrentsNewsProvider("k", http_get=get, clock=lambda: NOW, name_resolver=boom)
    provider.get_news("INFY.NS", as_of=AS_OF)
    assert seen[0]["query"].startswith('"INFY" AND (')


def test_lookback_over_7_days_is_rejected_with_a_clear_error() -> None:
    with pytest.raises(ValueError, match="at most 7 days"):
        CurrentsNewsProvider("k", lookback_days=8)


def test_currents_http_error_includes_the_response_body() -> None:
    provider, _ = _currents({}, status=400)
    with pytest.raises(RuntimeError, match="invalid parameter: start_date"):
        provider.get_news("TCS.NS", as_of=AS_OF)


def test_currents_non_ok_status_raises() -> None:
    provider, _ = _currents({"status": "error", "msg": "bad key"})
    with pytest.raises(RuntimeError, match="status"):
        provider.get_news("TCS.NS", as_of=AS_OF)


def test_currents_requires_an_api_key() -> None:
    with pytest.raises(ValueError):
        CurrentsNewsProvider("")


def test_currents_rejects_naive_as_of() -> None:
    provider, _ = _currents({"status": "ok", "news": []})
    with pytest.raises(ValueError):
        provider.get_news("TCS.NS", as_of=datetime(2025, 6, 30, 16, 0))
