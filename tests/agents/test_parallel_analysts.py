"""S2.6: selected analysts run in parallel, and a failed analyst leaves a gap (offline)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from test_market_analyst import FIXTURES, ScriptedModel, _cells, _state, _text_turn

from ta_in.agents import (
    ANALYST_NAMES,
    AnalystSpec,
    market_spec,
    parse_analysts,
    run_analysts,
)
from ta_in.data import IST, PointInTimeError
from ta_in.fixtures import FrozenDataProvider
from ta_in.llm import BudgetExceeded
from ta_in.state import AgentName, AgentState, AnalystReport

AGENT = {
    "market": (AgentName.MARKET_ANALYST, "market_report"),
    "news": (AgentName.NEWS_ANALYST, "news_report"),
    "fundamentals": (AgentName.FUNDAMENTALS_ANALYST, "fundamentals_report"),
}
AS_OF = datetime(2026, 11, 4, 16, 0, tzinfo=IST)


def base_state() -> AgentState:
    return AgentState(run_id="r1", ticker="AAA.NS", as_of=AS_OF)


def fake(
    name: str,
    *,
    before: Callable[[], object] | None = None,
    calls: list[str] | None = None,
) -> AnalystSpec:
    """A runner that writes an ok report (after running `before`, which may block or raise)."""
    agent, field = AGENT[name]

    def run(state: AgentState) -> AgentState:
        if calls is not None:
            calls.append(name)
        if before is not None:
            before()
        report = AnalystReport(analyst=name, text=f"{name} report")  # type: ignore[arg-type]
        return state.update(agent, **{field: report})

    return AnalystSpec(name=name, run=run)


def raising(name: str, exc: Exception) -> AnalystSpec:
    def boom() -> None:
        raise exc

    return fake(name, before=boom)


def specs(*items: AnalystSpec) -> dict[str, AnalystSpec]:
    return {s.name: s for s in items}


# --- criterion 1: they run concurrently ------------------------------------------------


def test_analysts_run_at_the_same_time() -> None:
    barrier = threading.Barrier(2, timeout=5)  # only opens if both runners are inside at once
    wait = barrier.wait
    all_specs = specs(fake("market", before=wait), fake("news", before=wait))
    out = run_analysts(base_state(), all_specs, ["market", "news"])
    assert out.market_report is not None and out.market_report.status == "ok"
    assert out.news_report is not None and out.news_report.status == "ok"


def test_sequential_running_would_have_failed_the_barrier() -> None:
    """Guards the test above: a runner alone at the barrier does time out."""
    barrier = threading.Barrier(2, timeout=0.2)
    out = run_analysts(base_state(), specs(fake("market", before=barrier.wait)), ["market"])
    assert out.market_report is not None and out.market_report.status == "gap"


# --- criterion 2: --analysts market,news runs only those two ---------------------------


def test_parse_analysts_accepts_a_comma_list() -> None:
    assert parse_analysts("market,news", ANALYST_NAMES) == ["market", "news"]


def test_parse_analysts_tolerates_spaces_case_and_repeats() -> None:
    assert parse_analysts(" News , MARKET,news ", ANALYST_NAMES) == ["news", "market"]


@pytest.mark.parametrize("bad", ["", " , ", "market,nonsense"])
def test_parse_analysts_rejects_empty_and_unknown_names(bad: str) -> None:
    with pytest.raises(ValueError, match="market"):
        parse_analysts(bad, ANALYST_NAMES)


def test_parse_analysts_says_when_an_analyst_is_not_built_yet() -> None:
    with pytest.raises(ValueError, match="not available"):
        parse_analysts("market,fundamentals", ["market", "news"])


def test_only_the_selected_analysts_run() -> None:
    calls: list[str] = []
    all_specs = specs(
        fake("market", calls=calls), fake("news", calls=calls), fake("fundamentals", calls=calls)
    )
    out = run_analysts(base_state(), all_specs, parse_analysts("market,news", ANALYST_NAMES))
    assert sorted(calls) == ["market", "news"]
    assert out.market_report is not None and out.news_report is not None
    assert out.fundamentals_report is None


def test_selecting_a_name_without_a_spec_is_an_error_before_anything_runs() -> None:
    calls: list[str] = []
    with pytest.raises(ValueError, match="fundamentals"):
        run_analysts(base_state(), specs(fake("market", calls=calls)), ["market", "fundamentals"])
    assert calls == []


def test_selecting_nobody_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        run_analysts(base_state(), specs(fake("market")), [])


# --- criterion 3: a forced failure leaves a visible gap, not a crash -------------------


def test_a_failing_analyst_becomes_a_gap_and_the_other_still_finishes() -> None:
    all_specs = specs(raising("market", RuntimeError("data source down")), fake("news"))
    out = run_analysts(base_state(), all_specs, ["market", "news"])
    gap = out.market_report
    assert gap is not None
    assert gap.status == "gap" and gap.analyst == "market" and gap.text == ""
    assert gap.gap_reason == "RuntimeError: data source down"
    assert out.news_report is not None and out.news_report.status == "ok"


def test_every_analyst_failing_still_returns_a_state_with_all_gaps() -> None:
    all_specs = specs(raising("market", ValueError("a")), raising("news", KeyError("b")))
    out = run_analysts(base_state(), all_specs, ["market", "news"])
    assert out.market_report is not None and out.market_report.status == "gap"
    assert out.news_report is not None and out.news_report.status == "gap"
    assert "KeyError" in (out.news_report.gap_reason or "")


def test_a_gap_returned_by_the_analyst_itself_passes_through() -> None:
    agent, field = AGENT["market"]
    own = AnalystReport(analyst="market", text="", status="gap", gap_reason="empty answer")

    def run(state: AgentState) -> AgentState:
        return state.update(agent, **{field: own})

    out = run_analysts(base_state(), {"market": AnalystSpec(name="market", run=run)}, ["market"])
    assert out.market_report == own


def test_a_runner_that_writes_no_report_is_a_gap() -> None:
    out = run_analysts(
        base_state(),
        {"market": AnalystSpec(name="market", run=lambda s: s)},
        ["market"],
    )
    assert out.market_report is not None
    assert out.market_report.status == "gap"
    assert "no report" in (out.market_report.gap_reason or "")


@pytest.mark.parametrize("fatal", [PointInTimeError("leak"), BudgetExceeded("spent")])
def test_look_ahead_and_budget_errors_are_not_hidden_as_gaps(fatal: Exception) -> None:
    calls: list[str] = []
    done = threading.Event()

    def finish() -> None:
        done.set()

    all_specs = specs(
        raising("market", fatal),
        fake("news", calls=calls, before=finish),
    )
    with pytest.raises(type(fatal)):
        run_analysts(base_state(), all_specs, ["market", "news"])
    assert done.is_set()  # the other analyst was not abandoned mid-run


# --- merging ---------------------------------------------------------------------------


def test_the_merge_follows_the_selection_order_not_the_finishing_order() -> None:
    news_done = threading.Event()
    all_specs = specs(
        fake("market", before=lambda: news_done.wait(5)),  # finishes last
        fake("news", before=news_done.set),  # finishes first
    )
    parallel = run_analysts(base_state(), all_specs, ["market", "news"])

    sequential = base_state()
    for name in ("market", "news"):
        sequential = fake(name).run(sequential)
    assert parallel == sequential


def test_a_runner_cannot_smuggle_in_someone_elses_field() -> None:
    other = AnalystReport(analyst="news", text="forged")

    def run(state: AgentState) -> AgentState:
        mine = AnalystReport(analyst="market", text="market report")
        return state.model_copy(update={"market_report": mine, "news_report": other})

    out = run_analysts(base_state(), {"market": AnalystSpec(name="market", run=run)}, ["market"])
    assert out.market_report is not None and out.market_report.text == "market report"
    assert out.news_report is None


def test_the_input_state_is_not_changed() -> None:
    state = base_state()
    run_analysts(state, specs(fake("market")), ["market"])
    assert state.market_report is None


# --- with the real market analyst ------------------------------------------------------


def test_the_real_market_analyst_runs_beside_a_failing_news_analyst() -> None:
    cells = _cells()
    if not cells:
        pytest.skip("real snapshots not frozen yet (run `freeze-fixtures`)")
    cell = cells[0]
    provider = FrozenDataProvider(FIXTURES)
    state = _state(provider, cell.ticker, cell.as_of)
    assert state.price_snapshot is not None
    price = state.price_snapshot.last_close
    model: Any = ScriptedModel([_text_turn(f"Closed at Rs {price:.2f}; no other levels quoted.")])

    all_specs = {
        "market": market_spec(model, provider, model="claude-sonnet-5-5"),
        "news": raising("news", RuntimeError("Currents is down")),
    }
    out = run_analysts(state, all_specs, ["market", "news"])
    assert out.market_report is not None and out.market_report.status == "ok"
    assert out.news_report is not None and out.news_report.status == "gap"
    assert "Currents is down" in (out.news_report.gap_reason or "")
    assert out.fundamentals_report is None


def test_market_spec_is_named_market() -> None:
    spec = market_spec(ScriptedModel([]), FrozenDataProvider(Path(".")), model="m")  # type: ignore[arg-type]
    assert spec.name == "market"
