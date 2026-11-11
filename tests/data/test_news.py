"""S1.4: dated company news up to as_of, deduplicated, as typed NewsItem."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ta_in.data import IST
from ta_in.news import NewsItem, NewsProvider, NewsService, canonical_url

TICKER = "RELIANCE.NS"
AS_OF = datetime(2025, 6, 30, 16, 0, tzinfo=IST)


def _item(
    title: str = "Reliance posts results",
    *,
    published: datetime = AS_OF - timedelta(hours=2),
    url: str = "https://example.com/a",
    source: str = "Wire",
    summary: str | None = None,
) -> NewsItem:
    return NewsItem(title=title, source=source, published_time=published, url=url, summary=summary)


class FakeProvider:
    def __init__(self, name: str, items: list[NewsItem]) -> None:
        self.name = name
        self._items = items
        self.calls: list[tuple[str, datetime]] = []

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        self.calls.append((ticker, as_of))
        return list(self._items)


class BrokenProvider:
    name = "Broken"

    def get_news(self, ticker: str, *, as_of: datetime) -> list[NewsItem]:
        raise RuntimeError("boom")


def _service(*providers: NewsProvider) -> tuple[NewsService, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []
    svc = NewsService(list(providers), event_sink=lambda n, p: events.append((n, p)))
    return svc, events


# --- NewsItem ---------------------------------------------------------------------------


def test_news_item_requires_timezone_aware_time() -> None:
    with pytest.raises(ValueError):
        NewsItem(
            title="t",
            source="s",
            published_time=datetime(2025, 6, 30, 10, 0),
            url="https://x.test/a",
        )


def test_news_item_normalises_to_utc_and_is_frozen() -> None:
    item = _item(published=datetime(2025, 6, 30, 15, 30, tzinfo=IST))
    assert item.published_time == datetime(2025, 6, 30, 10, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        item.title = "other"  # type: ignore[misc]


# --- criterion 1: items dated after as_of are dropped and logged ------------------------


def test_item_after_as_of_is_dropped_and_logged() -> None:
    ok = _item("kept", url="https://example.com/kept")
    late = _item("late", published=AS_OF + timedelta(minutes=1), url="https://example.com/late")
    svc, events = _service(FakeProvider("A", [ok, late]))

    out = svc.get_news(TICKER, as_of=AS_OF)

    assert [i.title for i in out] == ["kept"]
    drops = [p for n, p in events if n == "news.dropped_after_as_of"]
    assert len(drops) == 1
    assert drops[0]["title"] == "late"
    assert drops[0]["source"] == "Wire"
    assert drops[0]["ticker"] == TICKER


def test_item_exactly_at_as_of_is_kept() -> None:
    at = _item("at", published=AS_OF, url="https://example.com/at")
    svc, _ = _service(FakeProvider("A", [at]))
    assert [i.title for i in svc.get_news(TICKER, as_of=AS_OF)] == ["at"]


def test_cutoff_compares_instants_across_timezones() -> None:
    # 10:31 UTC is 16:01 IST: one minute after an as_of of 16:00 IST, so it must go.
    late = _item(
        "late",
        published=datetime(2025, 6, 30, 10, 31, tzinfo=UTC),
        url="https://example.com/late",
    )
    early = _item(
        "early",
        published=datetime(2025, 6, 30, 10, 29, tzinfo=UTC),
        url="https://example.com/early",
    )
    svc, _ = _service(FakeProvider("A", [late, early]))
    assert [i.title for i in svc.get_news(TICKER, as_of=AS_OF)] == ["early"]


def test_naive_as_of_is_rejected() -> None:
    svc, _ = _service(FakeProvider("A", []))
    with pytest.raises(ValueError):
        svc.get_news(TICKER, as_of=datetime(2025, 6, 30, 16, 0))


def test_as_of_is_passed_to_every_provider() -> None:
    a, b = FakeProvider("A", []), FakeProvider("B", [])
    svc, _ = _service(a, b)
    svc.get_news(TICKER, as_of=AS_OF)
    assert a.calls == [(TICKER, AS_OF)]
    assert b.calls == [(TICKER, AS_OF)]


# --- criterion 2: duplicates are removed ------------------------------------------------


def test_same_url_across_providers_is_deduplicated_earliest_wins() -> None:
    first = _item("Story", published=AS_OF - timedelta(hours=5), source="NSE")
    again = _item("Story", published=AS_OF - timedelta(hours=1), source="Currents")
    svc, events = _service(FakeProvider("A", [again]), FakeProvider("B", [first]))

    out = svc.get_news(TICKER, as_of=AS_OF)

    assert len(out) == 1
    assert out[0].source == "NSE"
    dedup = [p for n, p in events if n == "news.deduped"]
    assert dedup and dedup[0]["removed"] == 1


def test_tracking_params_and_trailing_slash_do_not_defeat_dedup() -> None:
    a = _item(url="https://Example.com/story/?utm_source=x&id=7#frag")
    b = _item(url="https://example.com/story?id=7&utm_campaign=y")
    svc, _ = _service(FakeProvider("A", [a, b]))
    assert len(svc.get_news(TICKER, as_of=AS_OF)) == 1


def test_different_query_ids_are_not_duplicates() -> None:
    a = _item(url="https://example.com/story?id=1")
    b = _item(url="https://example.com/story?id=2")
    svc, _ = _service(FakeProvider("A", [a, b]))
    assert len(svc.get_news(TICKER, as_of=AS_OF)) == 2


def test_items_without_url_dedup_on_title_and_time() -> None:
    a = _item("Same Title", url="")
    b = _item("  same title ", url="")
    c = _item("Other", url="")
    svc, _ = _service(FakeProvider("A", [a, b, c]))
    assert len(svc.get_news(TICKER, as_of=AS_OF)) == 2


def test_canonical_url_never_raises() -> None:
    assert canonical_url("") == ""
    assert canonical_url("http://[bad") == "http://[bad"


def test_result_is_sorted_oldest_first() -> None:
    new = _item("new", published=AS_OF - timedelta(hours=1), url="https://e.com/n")
    old = _item("old", published=AS_OF - timedelta(days=2), url="https://e.com/o")
    svc, _ = _service(FakeProvider("A", [new, old]))
    assert [i.title for i in svc.get_news(TICKER, as_of=AS_OF)] == ["old", "new"]


# --- criterion 3: empty results return an explicit empty list ---------------------------


def test_no_items_returns_empty_list_and_logs_it() -> None:
    svc, events = _service(FakeProvider("A", []))
    out = svc.get_news(TICKER, as_of=AS_OF)
    assert out == []
    assert isinstance(out, list)
    assert [n for n, _ in events] == ["news.empty"]


def test_everything_dropped_is_empty_and_counts_the_drops() -> None:
    late = _item(published=AS_OF + timedelta(days=1))
    svc, events = _service(FakeProvider("A", [late]))
    assert svc.get_news(TICKER, as_of=AS_OF) == []
    empty = next(p for n, p in events if n == "news.empty")
    assert empty["raw"] == 1
    assert empty["dropped_after_as_of"] == 1


# --- a failing provider is an explicit gap, not a crash ---------------------------------


def test_failed_provider_is_a_logged_gap_and_others_still_answer() -> None:
    svc, events = _service(BrokenProvider(), FakeProvider("A", [_item("ok")]))
    out = svc.get_news(TICKER, as_of=AS_OF)
    assert [i.title for i in out] == ["ok"]
    failed = [p for n, p in events if n == "news.provider_failed"]
    assert len(failed) == 1
    assert failed[0]["provider"] == "Broken"
    assert "boom" in failed[0]["error"]


def test_all_providers_failing_gives_empty_list_with_gaps() -> None:
    svc, events = _service(BrokenProvider())
    assert svc.get_news(TICKER, as_of=AS_OF) == []
    names = [n for n, _ in events]
    assert "news.provider_failed" in names
    assert "news.empty" in names


def test_event_sink_errors_never_break_a_run() -> None:
    def bad_sink(name: str, payload: dict) -> None:
        raise RuntimeError("sink down")

    svc = NewsService([FakeProvider("A", [_item()])], event_sink=bad_sink)
    assert len(svc.get_news(TICKER, as_of=AS_OF)) == 1


def test_service_requires_a_provider() -> None:
    with pytest.raises(ValueError):
        NewsService([])
