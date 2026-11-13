"""S4.5: propagate runs the pipeline on a frozen cell, records, replays, reports cost (offline).

No real model is called: one scripted SDK answers every agent, the real `LLMClient` records its
answers, and a second client replays them with an SDK that fails if it is ever called.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pipeline_sdk import TIER, ForbiddenSDK, PipelineSDK

from ta_in.config import Settings
from ta_in.fixtures import FrozenDataProvider, Manifest
from ta_in.llm import CacheMode, LLMClient, ResponseCache, TraceWriter, summarize
from ta_in.propagate import AllAnalystsFailedError, PropagateResult, propagate
from ta_in.schemas import PortfolioDecision

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _settings(debate: int = 1, risk: int = 1) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        ANTHROPIC_API_KEY="sk-test-key",
        budget_usd=1.0,
        debate_rounds=debate,
        risk_rounds=risk,
    )


def _client(
    tmp_path: Path, mode: CacheMode, sdk: Any, trace: TraceWriter | None = None
) -> LLMClient:
    return LLMClient(
        _settings(),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
        trace=trace,
    )


def _cell() -> tuple[str, datetime]:
    cell = Manifest.model_validate_json((FIXTURES / "manifest.json").read_text("utf-8")).cells[0]
    return cell.ticker, cell.as_of


def _run(client: LLMClient, **kw: Any) -> PropagateResult:
    ticker, as_of = _cell()
    kw.setdefault("settings", _settings())
    return propagate(ticker, as_of, client=client, provider=FrozenDataProvider(FIXTURES), **kw)


def _ticking() -> Any:
    values: Iterator[float] = iter([100.0, 112.5])
    return lambda: next(values)


# --- criterion 1: a fixture cell runs end to end, and replays exactly ------------------


def test_a_fixture_cell_runs_end_to_end(tmp_path: Path) -> None:
    sdk = PipelineSDK()
    result = _run(_client(tmp_path, CacheMode.OFF, sdk))

    state = result.state
    assert isinstance(result.decision, PortfolioDecision) and state.decision == result.decision
    for field in (
        "identity",
        "price_snapshot",
        "market_report",
        "news_report",
        "fundamentals_report",
        "plan",
        "proposal",
        "decision",
    ):
        assert getattr(state, field) is not None, field
    assert len(state.investment_debate) == 2 and len(state.risk_debate) == 3
    assert state.fundamentals_report is not None and state.fundamentals_report.status == "ok"
    # Past cell: statements are withheld, so the fundamentals analyst makes no model call.
    assert "fundamentals" not in sdk.roles()
    assert sorted(sdk.roles()) == sorted(
        ["market", "market", "news", "bull", "bear", "manager", "trader"]
        + ["aggressive", "neutral", "conservative", "portfolio"]
    )


def test_a_recorded_run_replays_identically_with_no_sdk_calls(tmp_path: Path) -> None:
    recorded = _run(_client(tmp_path, CacheMode.RECORD, PipelineSDK()), run_id="run-1")

    forbidden = ForbiddenSDK()
    replayed = _run(_client(tmp_path, CacheMode.REPLAY, forbidden), run_id="run-1")

    assert forbidden.calls == []
    assert replayed.state.to_json() == recorded.state.to_json()
    assert replayed.decision == recorded.decision
    assert replayed.llm_calls == recorded.llm_calls == 11


# --- criterion 2: total cost and time are reported -------------------------------------


def test_cost_tokens_calls_and_time_are_reported(tmp_path: Path) -> None:
    result = _run(_client(tmp_path, CacheMode.OFF, PipelineSDK()), clock=_ticking())

    assert result.llm_calls == 11 and result.cached_calls == 0
    assert result.input_tokens == 11 * 1000 and result.output_tokens == 11 * 500
    assert result.cost_usd > 0
    assert result.elapsed_s == pytest.approx(12.5)
    assert result.run_id


def test_a_replay_reports_zero_cost_and_all_calls_cached(tmp_path: Path) -> None:
    recorded = _run(_client(tmp_path, CacheMode.RECORD, PipelineSDK()))
    replayed = _run(_client(tmp_path, CacheMode.REPLAY, ForbiddenSDK()))
    assert recorded.cost_usd > 0
    assert replayed.cost_usd == 0.0
    assert replayed.cached_calls == replayed.llm_calls == recorded.llm_calls


def test_a_reused_client_reports_each_run_separately(tmp_path: Path) -> None:
    client = _client(tmp_path, CacheMode.OFF, PipelineSDK())
    first = _run(client)
    second = _run(client)
    assert second.cost_usd == pytest.approx(first.cost_usd)
    assert second.llm_calls == first.llm_calls == 11
    assert client.spent_usd == pytest.approx(first.cost_usd + second.cost_usd)


def test_a_different_run_id_is_the_only_difference_between_two_replays(tmp_path: Path) -> None:
    _run(_client(tmp_path, CacheMode.RECORD, PipelineSDK()), run_id="run-1")
    a = _run(_client(tmp_path, CacheMode.REPLAY, ForbiddenSDK()), run_id="run-1")
    b = _run(_client(tmp_path, CacheMode.REPLAY, ForbiddenSDK()), run_id="run-2")
    assert a.state.run_id == "run-1" and b.state.run_id == "run-2"
    assert a.state.model_dump(exclude={"run_id"}) == b.state.model_dump(exclude={"run_id"})


def test_a_run_id_that_disagrees_with_the_trace_is_rejected(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "traces", run_id="run-x")
    sdk = PipelineSDK()
    with pytest.raises(ValueError, match="run_id"):
        _run(_client(tmp_path, CacheMode.OFF, sdk, trace=trace), trace=trace, run_id="other")
    assert sdk.calls == []


def test_the_trace_matches_the_reported_totals(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "traces", run_id="run-x")
    result = _run(_client(tmp_path, CacheMode.OFF, PipelineSDK(), trace=trace), trace=trace)
    assert result.run_id == "run-x" and result.state.run_id == "run-x"
    assert result.trace_path == trace.path
    summary = summarize(trace.path)
    assert summary.total.calls == result.llm_calls
    assert summary.total.cost_usd == pytest.approx(result.cost_usd)


# --- tiers, settings, selection --------------------------------------------------------


def test_each_stage_uses_its_tier(tmp_path: Path) -> None:
    sdk = PipelineSDK()
    _run(_client(tmp_path, CacheMode.OFF, sdk))
    for call in sdk.calls:
        assert call["model"] == TIER[sdk.role_of(call["system"])]


@pytest.mark.parametrize(
    ("debate", "risk", "calls"), [(2, 1, 13), (0, 0, 6), (1, 0, 8), (0, 2, 12)]
)
def test_the_settings_rounds_are_honoured(
    tmp_path: Path, debate: int, risk: int, calls: int
) -> None:
    sdk = PipelineSDK()
    result = _run(_client(tmp_path, CacheMode.OFF, sdk), settings=_settings(debate, risk))
    assert len(result.state.investment_debate) == 2 * debate
    assert len(result.state.risk_debate) == 3 * risk
    assert len(sdk.calls) == result.llm_calls == calls
    assert result.state.plan is not None and result.state.decision is not None


def test_only_the_selected_analysts_run(tmp_path: Path) -> None:
    sdk = PipelineSDK()
    result = _run(_client(tmp_path, CacheMode.OFF, sdk), analysts=("market", "news"))
    assert result.state.fundamentals_report is None
    assert result.state.market_report is not None and result.state.news_report is not None


# --- failures --------------------------------------------------------------------------


def test_an_unknown_ticker_fails_before_any_model_call(tmp_path: Path) -> None:
    sdk = PipelineSDK()
    _, as_of = _cell()
    with pytest.raises(Exception, match="nothing frozen"):
        propagate(
            "NOSUCH.NS",
            as_of,
            settings=_settings(),
            client=_client(tmp_path, CacheMode.OFF, sdk),
            provider=FrozenDataProvider(FIXTURES),
        )
    assert sdk.calls == []


def test_a_naive_as_of_fails_before_any_call(tmp_path: Path) -> None:
    sdk = PipelineSDK()
    with pytest.raises(ValueError, match="timezone"):
        propagate(
            "RELIANCE.NS",
            datetime(2026, 10, 21, 16, 0),
            settings=_settings(),
            client=_client(tmp_path, CacheMode.OFF, sdk),
            provider=FrozenDataProvider(FIXTURES),
        )
    assert sdk.calls == []


def test_one_failing_analyst_leaves_a_visible_gap_and_the_run_finishes(tmp_path: Path) -> None:
    result = _run(
        _client(tmp_path, CacheMode.OFF, PipelineSDK(failing=("news",))),
        analysts=("market", "news"),
    )
    news = result.state.news_report
    assert news is not None and news.status == "gap" and "news is down" in (news.gap_reason or "")
    assert result.decision is not None


def test_all_analysts_failing_stops_the_run_before_the_debate(tmp_path: Path) -> None:
    sdk = PipelineSDK(failing=("market", "news"))
    with pytest.raises(AllAnalystsFailedError):
        _run(_client(tmp_path, CacheMode.OFF, sdk), analysts=("market", "news"))
    assert not {"bull", "bear", "manager", "trader", "portfolio"} & set(sdk.roles())
