"""S8.2: a live run's data is saved in fixture format and replays identically, offline."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from live_fakes import (
    FakeBFinance,
    FakeNewsProvider,
    FakePrices,
    _bars,
    _news_item,
    _sources,
)
from pipeline_sdk import ForbiddenSDK, PipelineSDK

from ta_in.cli import _frozen_cell_as_of
from ta_in.config import Settings
from ta_in.data import IST, PriceBar
from ta_in.fixtures import FrozenDataProvider
from ta_in.fixtures.models import Manifest
from ta_in.fundamentals import FundamentalsService, Withheld
from ta_in.live import LiveDataProvider, LiveSourceError
from ta_in.live_snapshot import RecordingProvider, write_snapshot
from ta_in.llm import CacheMode, LLMClient, ResponseCache
from ta_in.news import NewsService
from ta_in.propagate import propagate

TICKER = "INFY.NS"
LIVE_AS_OF = datetime(2026, 11, 13, 18, 11, 52, tzinfo=IST)  # seconds, like a real live run
SOURCES = {"mode": "live", "prices": "kite", "identity": "kite", "news": "currents"}


def _items(n: int = 4) -> list[Any]:
    return [_news_item(LIVE_AS_OF - timedelta(hours=h + 1), f"Infosys story {h}") for h in range(n)]


def _past_clock() -> Any:
    """A clock a month later: LIVE_AS_OF is then a past date, so fundamentals are Withheld."""
    return lambda: LIVE_AS_OF + timedelta(days=30)


def _live(
    sink: Any,
    *,
    prices: Any = None,
    news_error: Exception | None = None,
    clock: Any = None,
) -> LiveDataProvider:
    sources = _sources(
        prices=prices or FakePrices(_bars(LIVE_AS_OF.date(), 80)),
        news=lambda s: NewsService([FakeNewsProvider(_items(), error=news_error)], event_sink=s),
        fundamentals=FundamentalsService([FakeBFinance()], clock=clock or _past_clock()),
    )
    return LiveDataProvider(sources, event_sink=sink)


def _recorder(**kw: Any) -> RecordingProvider:
    return RecordingProvider(lambda sink: _live(sink, **kw))


def _settings() -> Settings:
    return Settings(_env_file=None, ANTHROPIC_API_KEY="sk-test", budget_usd=1.0)  # type: ignore[call-arg]


def _client(tmp_path: Path, mode: CacheMode, sdk: Any) -> LLMClient:
    return LLMClient(
        _settings(),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
    )


def _run_folder(tmp_path: Path, name: str = "live-1") -> Path:
    folder = tmp_path / "results" / TICKER / "2026-11-13" / name
    folder.mkdir(parents=True)
    return folder


def _touch_all(recorder: RecordingProvider, ticker: str = TICKER) -> None:
    recorder.identity(ticker, as_of=LIVE_AS_OF)
    recorder.prices(ticker, as_of=LIVE_AS_OF)
    recorder.news(ticker, as_of=LIVE_AS_OF)
    recorder.fundamentals(ticker, as_of=LIVE_AS_OF)


# --- criterion 1: the saved folder loads and a re-run from it is identical --------------


def test_a_live_run_replays_identically_from_its_saved_snapshot(tmp_path: Path) -> None:
    recorder = _recorder()
    recorded = propagate(
        TICKER,
        LIVE_AS_OF,
        settings=_settings(),
        client=_client(tmp_path, CacheMode.RECORD, PipelineSDK()),
        provider=recorder,
        run_id="live-1",
    )
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )

    forbidden = ForbiddenSDK()
    replayed = propagate(
        TICKER,
        LIVE_AS_OF,
        settings=_settings(),
        client=_client(tmp_path, CacheMode.REPLAY, forbidden),
        provider=FrozenDataProvider(data),
        run_id="live-1",
    )

    assert forbidden.calls == []
    assert replayed.state.to_json() == recorded.state.to_json()
    assert replayed.decision == recorded.decision
    assert replayed.cost_usd == 0.0 and replayed.llm_calls == recorded.llm_calls == 11


def test_the_saved_folder_reads_back_what_the_live_run_saw(tmp_path: Path) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )
    frozen = FrozenDataProvider(data)
    live = _live(None)

    assert frozen.identity(TICKER, as_of=LIVE_AS_OF) == live.identity(TICKER, as_of=LIVE_AS_OF)
    assert list(frozen.prices(TICKER, as_of=LIVE_AS_OF)) == list(
        live.prices(TICKER, as_of=LIVE_AS_OF)
    )
    assert list(frozen.news(TICKER, as_of=LIVE_AS_OF)) == list(live.news(TICKER, as_of=LIVE_AS_OF))
    assert frozen.fundamentals(TICKER, as_of=LIVE_AS_OF) == live.fundamentals(
        TICKER, as_of=LIVE_AS_OF
    )


def test_the_manifest_validates_and_the_cli_can_find_the_cell(tmp_path: Path) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    data = write_snapshot(
        recorder,
        _run_folder(tmp_path),
        ticker=TICKER,
        frozen_at=LIVE_AS_OF,
        sources=SOURCES,
    )
    manifest = Manifest.model_validate_json((data / "manifest.json").read_text("utf-8"))
    assert [c.ticker for c in manifest.cells] == [TICKER]
    assert manifest.cells[0].as_of == LIVE_AS_OF and manifest.cells[0].news_count == 4
    assert manifest.cells[0].fundamentals == "withheld"
    assert manifest.sources["mode"] == "live" and manifest.failures == []
    # `ta-in run --fixtures <data> --date 2026-11-13` uses exactly this lookup.
    day = datetime(2026, 11, 13, 16, 0, tzinfo=IST)
    assert _frozen_cell_as_of(data, TICKER, day) == LIVE_AS_OF


def test_withheld_fundamentals_and_a_news_gap_survive_the_snapshot(tmp_path: Path) -> None:
    recorder = _recorder(news_error=RuntimeError("currents down"))
    _touch_all(recorder)
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )
    frozen = FrozenDataProvider(data)
    assert isinstance(frozen.fundamentals(TICKER, as_of=LIVE_AS_OF), Withheld)
    assert list(frozen.news(TICKER, as_of=LIVE_AS_OF)) == []
    cell = json.loads(
        (data / "snapshots" / "cells" / TICKER / "2026-11-13.json").read_text("utf-8")
    )
    assert [e["event"] for e in cell["news_events"]].count("news.provider_failed") == 1
    assert "currents down" in cell["news_events"][0]["error"]


def test_current_fundamentals_are_saved_as_data(tmp_path: Path) -> None:
    recorder = _recorder(clock=lambda: LIVE_AS_OF)
    _touch_all(recorder)
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )
    funds = FrozenDataProvider(data).fundamentals(TICKER, as_of=LIVE_AS_OF)
    assert not isinstance(funds, Withheld) and "2025-03-31" in funds.income_statement
    manifest = Manifest.model_validate_json((data / "manifest.json").read_text("utf-8"))
    assert manifest.cells[0].fundamentals == "data"


def test_a_ticker_with_an_ampersand_works_as_a_folder_and_file_name(tmp_path: Path) -> None:
    recorder = _recorder()
    _touch_all(recorder, "M&M.NS")
    folder = tmp_path / "results" / "M&M.NS" / "2026-11-13" / "mm-1"
    folder.mkdir(parents=True)
    data = write_snapshot(recorder, folder, ticker="M&M.NS", frozen_at=LIVE_AS_OF, sources=SOURCES)
    assert FrozenDataProvider(data).identity("M&M.NS", as_of=LIVE_AS_OF).ticker == "M&M.NS"


# --- recording behaviour ---------------------------------------------------------------


def test_many_threads_record_one_result_and_no_divergence(tmp_path: Path) -> None:
    recorder = _recorder()
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(20):
                recorder.prices(TICKER, as_of=LIVE_AS_OF)
                recorder.news(TICKER, as_of=LIVE_AS_OF)
        except BaseException as err:  # noqa: BLE001
            errors.append(err)

    threads = [threading.Thread(target=work) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    recorder.identity(TICKER, as_of=LIVE_AS_OF)
    assert errors == [] and recorder.divergences == []
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )
    assert len(FrozenDataProvider(data).prices(TICKER, as_of=LIVE_AS_OF)) == 80


class ChangingPrices:
    """Returns different closes on every call, like data that moves during a run."""

    def __init__(self) -> None:
        self.calls = 0

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        self.calls += 1
        return [
            bar.model_copy(update={"close": bar.close + self.calls})
            for bar in _bars(LIVE_AS_OF.date(), 40)
        ]


def test_data_that_changes_during_a_run_keeps_the_first_result_and_notes_it(
    tmp_path: Path,
) -> None:
    recorder = _recorder(prices=ChangingPrices())
    first = list(recorder.prices(TICKER, as_of=LIVE_AS_OF))
    second = list(recorder.prices(TICKER, as_of=LIVE_AS_OF))
    assert first != second  # the source really changed
    assert len(recorder.divergences) == 1 and "prices" in recorder.divergences[0]
    recorder.identity(TICKER, as_of=LIVE_AS_OF)
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )
    assert list(FrozenDataProvider(data).prices(TICKER, as_of=LIVE_AS_OF)) == first
    cell = json.loads(
        (data / "snapshots" / "cells" / TICKER / "2026-11-13.json").read_text("utf-8")
    )
    assert any("changed between calls" in e for e in cell["errors"])


def test_an_inner_failure_is_re_raised_unchanged_and_recorded(tmp_path: Path) -> None:
    recorder = _recorder(prices=FakePrices(error=ConnectionError("kite unreachable")))
    with pytest.raises(LiveSourceError, match="kite unreachable"):
        recorder.prices(TICKER, as_of=LIVE_AS_OF)
    assert any("prices failed" in e for e in recorder.errors)
    recorder.identity(TICKER, as_of=LIVE_AS_OF)
    with pytest.raises(ValueError, match="prices"):  # nothing usable was recorded
        write_snapshot(
            recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
        )


def test_nothing_recorded_writes_nothing(tmp_path: Path) -> None:
    folder = _run_folder(tmp_path)
    with pytest.raises(ValueError, match="nothing"):
        write_snapshot(_recorder(), folder, ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES)
    assert list(folder.iterdir()) == []


# --- criterion 2: never overwritten; atomic --------------------------------------------


def test_a_snapshot_is_never_overwritten(tmp_path: Path) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    folder = _run_folder(tmp_path)
    data = write_snapshot(recorder, folder, ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES)
    before = {
        p.relative_to(data).as_posix(): p.read_bytes() for p in data.rglob("*") if p.is_file()
    }

    with pytest.raises(FileExistsError):
        write_snapshot(recorder, folder, ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES)

    after = {p.relative_to(data).as_posix(): p.read_bytes() for p in data.rglob("*") if p.is_file()}
    assert after == before


def test_a_crash_while_writing_leaves_no_snapshot_and_no_temp_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    folder = _run_folder(tmp_path)
    original = Path.write_text
    calls = {"n": 0}

    def flaky(self: Path, *args: Any, **kwargs: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        write_snapshot(recorder, folder, ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES)
    monkeypatch.undo()

    assert list(folder.iterdir()) == []  # neither data/ nor a temp folder
    # and a retry succeeds, because nothing half-written was left behind
    assert write_snapshot(
        recorder, folder, ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    ).exists()


# --- safety ----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../x", "a b", "", "x/y", ".NS"])
def test_an_unsafe_ticker_is_refused_before_anything_is_written(tmp_path: Path, bad: str) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    folder = _run_folder(tmp_path)
    with pytest.raises(ValueError, match="ticker"):
        write_snapshot(recorder, folder, ticker=bad, frozen_at=LIVE_AS_OF, sources=SOURCES)
    assert list(folder.iterdir()) == []


def test_a_missing_run_folder_is_refused(tmp_path: Path) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    with pytest.raises(ValueError, match="run folder"):
        write_snapshot(
            recorder, tmp_path / "nope", ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
        )
    assert not (tmp_path / "nope").exists()


def test_no_credential_name_or_value_is_written(tmp_path: Path) -> None:
    recorder = _recorder()
    _touch_all(recorder)
    data = write_snapshot(
        recorder, _run_folder(tmp_path), ticker=TICKER, frozen_at=LIVE_AS_OF, sources=SOURCES
    )
    text = " ".join(p.read_text("utf-8") for p in data.rglob("*.json")).lower()
    for marker in ("api_key", "access_token", "secret", "authorization"):
        assert marker not in text


def test_the_module_never_mentions_an_order_method() -> None:
    source = (Path(__file__).parents[2] / "src" / "ta_in" / "live_snapshot.py").read_text("utf-8")
    for name in ("place_order", "modify_order", "cancel_order"):
        assert name not in source
