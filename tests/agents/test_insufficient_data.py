"""S2.7: thin evidence is flagged instead of analysed (offline, no model)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from ta_in.agents import (
    AnalystSpec,
    insufficient_data_report,
    is_thin,
    run_analysts,
    run_gated,
)
from ta_in.config import Settings
from ta_in.data import IST
from ta_in.state import AgentName, AgentState, AnalystReport

MINIMUM = 3
AS_OF = datetime(2026, 11, 4, 16, 0, tzinfo=IST)


def items(n: int) -> list[str]:
    return [f"item {i}" for i in range(n)]


def real_analysis() -> AnalystReport:
    return AnalystReport(analyst="news", text="Three sources agree on a results beat.")


def must_not_run() -> AnalystReport:
    raise AssertionError("the analysis ran on thin evidence")


# --- the acceptance criterion ----------------------------------------------------------


@pytest.mark.parametrize("found", [0, 1, 2])
def test_fewer_than_the_minimum_carries_the_insufficient_data_flag(found: int) -> None:
    report = run_gated("news", items(found), MINIMUM, must_not_run)
    assert report.insufficient_data is True
    assert report.analyst == "news"
    assert report.status == "ok"  # nothing failed; the evidence was thin


@pytest.mark.parametrize("found", [3, 4, 20])
def test_the_minimum_or_more_is_analysed_normally(found: int) -> None:
    report = run_gated("news", items(found), MINIMUM, real_analysis)
    assert report == real_analysis()
    assert report.insufficient_data is False


def test_thin_evidence_never_reaches_the_analysis() -> None:
    calls: list[int] = []

    def analyse() -> AnalystReport:
        calls.append(1)
        return real_analysis()

    run_gated("news", items(2), MINIMUM, analyse)
    assert calls == []
    run_gated("news", items(3), MINIMUM, analyse)
    assert calls == [1]


def test_the_flagged_report_says_plainly_what_was_found() -> None:
    text = run_gated("news", items(1), MINIMUM, must_not_run).text
    assert "Insufficient data" in text
    assert "1 news item," in text  # singular
    assert "minimum is 3" in text
    assert "No analysis was made" in text
    assert "2 news items," in run_gated("news", items(2), MINIMUM, must_not_run).text


def test_the_noun_is_configurable_for_other_evidence() -> None:
    report = insufficient_data_report("fundamentals", 0, 2, what="annual statements")
    assert report.analyst == "fundamentals"
    assert "0 annual statements" in report.text


def test_is_thin_boundaries() -> None:
    assert is_thin(2, 3) and not is_thin(3, 3) and not is_thin(4, 3)


def test_a_generator_of_items_is_counted() -> None:
    assert run_gated("news", (i for i in range(2)), MINIMUM, must_not_run).insufficient_data


# --- the configured minimum ------------------------------------------------------------


def _settings(**env: Any) -> Settings:
    return Settings(_env_file=None, ANTHROPIC_API_KEY="sk-ant-FAKE-0123456789", **env)  # type: ignore[call-arg]


def test_the_default_minimum_comes_from_settings() -> None:
    minimum = _settings().min_news_items
    assert minimum == 3
    assert run_gated("news", items(2), minimum, must_not_run).insufficient_data


def test_a_different_configured_minimum_changes_the_outcome_for_the_same_items() -> None:
    same = items(4)
    assert (
        run_gated("news", same, _settings().min_news_items, real_analysis).insufficient_data
        is False
    )
    stricter = _settings(min_news_items=5).min_news_items
    assert run_gated("news", same, stricter, must_not_run).insufficient_data is True


# --- the flag survives the pipeline ----------------------------------------------------


def _news_spec(found: int) -> AnalystSpec:
    def run(state: AgentState) -> AgentState:
        report = run_gated("news", items(found), MINIMUM, real_analysis)
        return state.update(AgentName.NEWS_ANALYST, news_report=report)

    return AnalystSpec(name="news", run=run)


def test_the_flag_survives_parallel_running_and_the_state_round_trip() -> None:
    state = AgentState(run_id="r1", ticker="AAA.NS", as_of=AS_OF)
    out = run_analysts(state, {"news": _news_spec(1)}, ["news"])
    assert out.news_report is not None and out.news_report.insufficient_data is True
    again = AgentState.from_json(out.to_json())
    assert again.news_report is not None and again.news_report.insufficient_data is True
    assert again == out


def test_enough_news_leaves_the_flag_off_in_the_state() -> None:
    state = AgentState(run_id="r1", ticker="AAA.NS", as_of=AS_OF)
    out = run_analysts(state, {"news": _news_spec(5)}, ["news"])
    assert out.news_report is not None and out.news_report.insufficient_data is False
