"""S8.3: `ta-in run --live` end to end with fake sources (offline), and replay of a saved run."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from live_fakes import (
    FakeBFinance,
    FakeIdentity,
    FakeNewsProvider,
    FakePrices,
    _bar,
    _news_item,
    _sources,
)
from pipeline_sdk import ForbiddenSDK, PipelineSDK

from ta_in.cli import main
from ta_in.config import Settings
from ta_in.data import IST, PriceBar, UnknownTickerError
from ta_in.data.prices import effective_end
from ta_in.fixtures.models import Manifest
from ta_in.fundamentals import FundamentalsService
from ta_in.live import LiveSources
from ta_in.news import NewsService
from ta_in.reports import FILES

NOW = datetime(2026, 11, 17, 18, 11, 52, tzinfo=IST)
FAKE_ANTHROPIC = "sk-ant-SECRET-MARKER-111"
FAKE_KITE_KEY = "KITE-KEY-SECRET-222"


def clock() -> datetime:
    return NOW


class PricesUpTo:
    """Like the real provider: only the bars visible at `as_of` (never a later one)."""

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        end = effective_end(as_of)
        return [_bar(end - timedelta(days=i), 1100.0 + i) for i in range(79, -1, -1)]


def _items() -> list[Any]:
    return [_news_item(NOW - timedelta(hours=h + 1), f"Infosys story {h}") for h in range(4)]


def make_factory(
    *,
    prices: Any = None,
    identity: Any = None,
    news_error: Exception | None = None,
    degraded: tuple[str, ...] = (),
    fundamentals_clock: Any = None,
    calls: list[str] | None = None,
) -> Any:
    def factory(settings: Settings, *, clock: Any) -> LiveSources:
        if calls is not None:
            calls.append("factory")
        sources = _sources(
            identity=identity or FakeIdentity(),
            prices=prices or PricesUpTo(),
            news=lambda sink: NewsService(
                [FakeNewsProvider(_items(), error=news_error)], event_sink=sink
            ),
            fundamentals=FundamentalsService(
                [FakeBFinance("2026-03-31")],
                clock=fundamentals_clock or (lambda: NOW + timedelta(days=60)),  # as_of is past
            ),
            degraded=degraded,
        )
        return sources

    return factory


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("TA_") or name in {"ANTHROPIC_API_KEY", "CURRENTS_API_KEY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    monkeypatch.setenv("TA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TA_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("TA_RESULTS_DIR", str(tmp_path / "results"))


def run(
    capsys: pytest.CaptureFixture[str],
    *argv: str,
    sdk: Any = None,
    factory: Any = None,
) -> tuple[int, str, str]:
    code = main(["run", *argv], sdk=sdk, clock=clock, live_factory=factory)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


LIVE = ("--live", "--ticker", "INFY.NS", "--run-id", "live-1")


# --- criterion 1: a live run completes end to end --------------------------------------


def test_a_live_run_prints_the_decision_the_sources_cost_and_time(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    sdk = PipelineSDK()
    code, out, err = run(capsys, *LIVE, sdk=sdk, factory=make_factory())

    assert code == 0 and "Traceback" not in err
    assert "Rating: Overweight" in out and "Executive summary:" in out
    assert "Number check:" in out
    assert "Data: LIVE, as of 2026-11-17 18:11 IST" in out
    assert "identity:" in out and "Kite" in out and "INFOSYS" in out
    assert "prices:" in out and "last bar 2026-11-17" in out
    assert "news:" in out and "Currents" in out and "4 items" in out
    assert "fundamentals:" in out and "withheld" in out
    assert "Run report" in out and "calls:" in out and "cost:" in out and "time:" in out
    assert len(sdk.calls) == 11
    # the order of the output: decision, number check, data, run report
    assert out.index("Number check") < out.index("Data: LIVE") < out.index("Run report")


def test_the_results_and_the_data_snapshot_are_saved_together(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(capsys, *LIVE, sdk=PipelineSDK(), factory=make_factory())
    assert code == 0
    folder = tmp_path / "results" / "INFY.NS" / "2026-11-17" / "live-1"
    assert FILES and {p.name for p in folder.iterdir()} == {*FILES, "data"}
    manifest = Manifest.model_validate_json((folder / "data" / "manifest.json").read_text("utf-8"))
    assert manifest.cells[0].as_of == NOW and manifest.sources["mode"] == "live"
    meta = json.loads((folder / "run.json").read_text("utf-8"))
    assert meta["as_of"] == NOW.isoformat()
    assert str(folder / "data") in out  # the snapshot path is printed
    assert any((tmp_path / "cache").glob("*.json"))  # model answers were recorded


def test_the_model_answers_are_recorded_by_default_in_live_mode(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(capsys, *LIVE, sdk=PipelineSDK(), factory=make_factory())
    assert code == 0 and "recording" in out.lower()
    assert len(list((tmp_path / "cache").glob("*.json"))) > 5


def test_a_past_day_runs_at_the_close_and_says_what_is_degraded(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(
        capsys, *LIVE, "--date", "2026-11-10", sdk=PipelineSDK(), factory=make_factory()
    )
    assert code == 0
    assert "as of 2026-11-10 16:00 IST" in out
    assert "fundamentals will be withheld" in out
    assert (tmp_path / "results" / "INFY.NS" / "2026-11-10" / "live-1").is_dir()


def test_a_day_older_than_thirty_days_also_says_news_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(
        capsys, *LIVE, "--date", "2026-10-01", sdk=PipelineSDK(), factory=make_factory()
    )
    assert code == 0 and "news is unavailable" in out


def test_todays_date_means_now_not_four_pm(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(
        capsys, *LIVE, "--date", "2026-11-17", sdk=PipelineSDK(), factory=make_factory()
    )
    assert code == 0 and "as of 2026-11-17 18:11 IST" in out


@pytest.mark.parametrize("date", ["2026-11-18", "2026-11-17T23:00+05:30"])
def test_a_date_in_the_future_is_refused(capsys: pytest.CaptureFixture[str], date: str) -> None:
    sdk = PipelineSDK()
    code, out, err = run(capsys, *LIVE, "--date", date, sdk=sdk, factory=make_factory())
    assert code == 2 and out == "" and "future" in err and "Traceback" not in err
    assert sdk.calls == []


def test_a_ticker_without_the_ns_suffix_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    sdk = PipelineSDK()
    code, _, err = run(capsys, "--live", "--ticker", "INFY", sdk=sdk, factory=make_factory())
    assert code == 2 and ".NS" in err and sdk.calls == []


def test_degraded_news_is_named_in_the_data_block(capsys: pytest.CaptureFixture[str]) -> None:
    factory = make_factory(
        news_error=RuntimeError("CURRENTS_API_KEY not set"),
        degraded=("news: CURRENTS_API_KEY not set",),
    )
    code, out, _ = run(capsys, *LIVE, sdk=PipelineSDK(), factory=factory)
    assert code == 0 and "news: degraded" in out and "CURRENTS_API_KEY not set" in out


def test_current_fundamentals_are_shown_as_live_data(capsys: pytest.CaptureFixture[str]) -> None:
    factory = make_factory(fundamentals_clock=clock)  # as_of is now, so statements are current
    code, out, _ = run(capsys, *LIVE, sdk=PipelineSDK(), factory=factory)
    assert code == 0 and "bfinance" in out and "2026-03-31" in out


def test_the_existing_flags_work_in_live_mode(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    sdk = PipelineSDK()
    elsewhere = tmp_path / "elsewhere"
    code, _, _ = run(
        capsys,
        *LIVE,
        "--rounds",
        "0",
        "--risk-rounds",
        "0",
        "--results-dir",
        str(elsewhere),
        sdk=sdk,
        factory=make_factory(),
    )
    assert code == 0 and len(sdk.calls) == 6
    assert (elsewhere / "INFY.NS" / "2026-11-17" / "live-1" / "data").is_dir()
    assert not (tmp_path / "results").exists()


def test_an_unsafe_run_id_is_refused_before_anything_runs(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    calls: list[str] = []
    code, _, err = run(
        capsys,
        "--live",
        "--ticker",
        "INFY.NS",
        "--run-id",
        "../x",
        sdk=PipelineSDK(),
        factory=make_factory(calls=calls),
    )
    assert code == 2 and "--run-id" in err and calls == []


def test_a_snapshot_write_failure_exits_one_after_the_decision_is_saved(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr("ta_in.live_snapshot.write_snapshot", boom)
    code, out, err = run(capsys, *LIVE, sdk=PipelineSDK(), factory=make_factory())
    assert code == 1 and "Rating: Overweight" in out
    assert "could not save the data snapshot" in err and "Traceback" not in err
    assert (tmp_path / "results" / "INFY.NS" / "2026-11-17" / "live-1" / "decision.json").exists()


# --- criterion 2: missing or expired credentials ---------------------------------------


def test_missing_kite_credentials_are_named_with_no_value_and_no_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sdk = PipelineSDK()
    code, out, err = run(capsys, *LIVE, sdk=sdk)  # the real factory
    assert code == 2 and out == "" and "Traceback" not in err
    assert "TA_KITE_API_KEY" in err and "TA_KITE_ACCESS_TOKEN" in err
    assert FAKE_ANTHROPIC not in err and sdk.calls == []


def test_every_missing_credential_is_named_in_one_message(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    code, out, err = run(capsys, *LIVE, sdk=PipelineSDK())
    assert code == 2 and out == ""
    for name in ("ANTHROPIC_API_KEY", "TA_KITE_API_KEY", "TA_KITE_ACCESS_TOKEN"):
        assert name in err
    assert "Traceback" not in err


def test_one_missing_credential_names_only_that_one(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TA_KITE_API_KEY", FAKE_KITE_KEY)
    code, _, err = run(capsys, *LIVE, sdk=PipelineSDK())
    assert code == 2 and "TA_KITE_ACCESS_TOKEN" in err
    assert "TA_KITE_API_KEY" not in err and FAKE_KITE_KEY not in err


def test_an_expired_kite_token_is_a_clear_error_before_any_model_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class TokenException(Exception):  # recognised by name, like kiteconnect's
        pass

    sdk = PipelineSDK()
    factory = make_factory(prices=FakePrices(error=TokenException("Incorrect api_key")))
    code, out, err = run(capsys, *LIVE, sdk=sdk, factory=factory)
    assert code == 1 and out == ""
    assert "expired" in err and "generate a new one" in err and "Traceback" not in err
    assert sdk.calls == []


def test_an_unknown_ticker_is_a_clear_error(capsys: pytest.CaptureFixture[str]) -> None:
    sdk = PipelineSDK()
    factory = make_factory(identity=FakeIdentity(UnknownTickerError("'ZZZ' not found")))
    code, _, err = run(capsys, "--live", "--ticker", "ZZZ.NS", sdk=sdk, factory=factory)
    assert code == 2 and "unknown ticker" in err.lower() and "Traceback" not in err
    assert sdk.calls == []


def test_no_secret_value_appears_in_any_output(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, err = run(capsys, *LIVE, sdk=PipelineSDK(), factory=make_factory())
    assert FAKE_ANTHROPIC not in out + err and FAKE_KITE_KEY not in out + err


# --- criterion 3: --live with --fixtures is refused; replay makes no network call ------


def test_live_and_fixtures_together_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    sdk = PipelineSDK()
    code, out, err = run(capsys, *LIVE, "--fixtures", "tests/fixtures", sdk=sdk)
    assert code == 2 and out == "" and "not allowed with" in err
    assert sdk.calls == []


def test_a_saved_live_run_replays_with_no_network_no_credentials_and_no_factory(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, recorded, _ = run(capsys, *LIVE, sdk=PipelineSDK(), factory=make_factory())
    assert code == 0

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the network was used during a live replay")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # a replay needs no key
    calls: list[str] = []
    forbidden = ForbiddenSDK()
    code, replayed, err = run(
        capsys,
        "--live",
        "--replay",
        "--ticker",
        "INFY.NS",
        "--run-id",
        "live-1",
        sdk=forbidden,
        factory=make_factory(calls=calls),
    )

    assert code == 0 and err == "", err
    assert forbidden.calls == [] and calls == []  # no model call and no live source built

    def decision(text: str) -> str:
        return text.split("Number check")[0]

    assert decision(replayed) == decision(recorded)
    assert "(already saved)" in replayed


def test_a_live_replay_needs_a_run_id_and_a_saved_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(capsys, "--live", "--replay", "--ticker", "INFY.NS")
    assert code == 2 and "--run-id" in err
    code, _, err = run(capsys, "--live", "--replay", "--ticker", "INFY.NS", "--run-id", "nope-1")
    assert code == 2 and "no saved live run" in err and "Traceback" not in err


def test_the_replay_cache_mode_is_refused_for_a_live_run_that_calls_the_model(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TA_CACHE_MODE", "replay")
    sdk = PipelineSDK()
    code, _, err = run(capsys, *LIVE, sdk=sdk, factory=make_factory())
    assert code == 2 and "replay" in err and sdk.calls == []
