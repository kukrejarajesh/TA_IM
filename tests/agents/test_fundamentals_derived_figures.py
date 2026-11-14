"""Fundamentals: honest derived figures are no longer thrown away by the grounding check.

Background: the first real current-date run (`infy-current-1`, 2026-11-14) lost its fundamentals
report because the strict check rejected a correct sum (Common Stock + Retained Earnings = Rs 92,852
crore) and two cash outflows written as positive amounts (Financing Cash Flow -39,786 and -24,161).
"""

from __future__ import annotations

import re
from typing import Any

from test_fundamentals_analyst import MODEL
from test_market_analyst import FIXTURES, ScriptedModel, _cells, _state, _text_turn, _tool_turn

from ta_in.agents import run_fundamentals_analyst, unverified_crore_figures
from ta_in.fixtures import FrozenDataProvider
from ta_in.llm import LLMTurn
from ta_in.state import AgentState


def _infy() -> tuple[FrozenDataProvider, AgentState]:
    cell = next(
        c for c in _cells() if c.ticker == "INFY.NS" and c.as_of.date().isoformat() == "2026-11-11"
    )
    provider = FrozenDataProvider(FIXTURES)
    return provider, _state(provider, cell.ticker, cell.as_of)


def _row(prompt: str, name: str) -> dict[str, float]:
    """The shown values of one statement row, by period, parsed from the prompt."""
    line = next(ln for ln in prompt.splitlines() if ln.startswith(f"{name} (Rs crore):"))
    cells = line.split(":", 1)[1].split(";")
    out: dict[str, float] = {}
    for cell in cells:
        match = re.match(r"\s*(\d{4}-\d{2}-\d{2})=(-?[\d,]+\.\d{2})", cell)
        assert match is not None
        out[match.group(1)] = float(match.group(2).replace(",", ""))
    return out


def _prompt(messages: list[dict[str, Any]]) -> str:
    content = messages[0]["content"]
    assert isinstance(content, str)
    return content


def _numbers(messages: list[dict[str, Any]]) -> dict[str, float]:
    prompt = _prompt(messages)
    equity = _row(prompt, "Common Stock")
    retained = _row(prompt, "Retained Earnings")
    financing = _row(prompt, "Financing Cash Flow")
    latest = "2026-03-31"  # the year in the real failure (the newest balance sheet is a half-year)
    years = sorted(financing, reverse=True)
    return {
        "common": equity[latest],
        "retained": retained[latest],
        "outflow_now": financing[years[0]],
        "outflow_before": financing[years[1]],
    }


def _first_answer(messages: list[dict[str, Any]]) -> LLMTurn:
    n = _numbers(messages)
    return _text_turn(
        f"Shareholders' funds were Rs {n['common'] + n['retained']:,.0f} crore. Financing "
        f"outflows were Rs {abs(n['outflow_now']):,.0f} crore and Rs "
        f"{abs(n['outflow_before']):,.0f} crore in the last two years. Neutral."
    )


def _calculate(messages: list[dict[str, Any]]) -> LLMTurn:
    n = _numbers(messages)
    return _tool_turn(
        ("c1", "calculate", {"expression": f"{n['common']:.2f} + {n['retained']:.2f}"})
    )


def _second_answer(messages: list[dict[str, Any]]) -> LLMTurn:
    n = _numbers(messages)
    total = n["common"] + n["retained"]
    return _text_turn(
        f"Shareholders' funds were Rs {total:,.0f} crore. Financing outflows were Rs "
        f"{abs(n['outflow_now']):,.0f} crore and Rs {abs(n['outflow_before']):,.0f} crore. Neutral."
    )


# --- the real regression ---------------------------------------------------------------


def test_the_infy_current_run_is_no_longer_a_gap() -> None:
    provider, state = _infy()
    model = ScriptedModel([_first_answer, _calculate, _second_answer])
    report = run_fundamentals_analyst(model, state, provider, model=MODEL).fundamentals_report

    assert report is not None and report.status == "ok", report and report.gap_reason
    assert "crore" in report.text
    assert (
        len(model.calls) == 3
    )  # the failed report, then the retry run (calculate, then the answer)


def test_the_outflows_alone_pass_by_absolute_value_but_the_unshown_sum_is_retried() -> None:
    provider, state = _infy()
    model = ScriptedModel([_first_answer, _calculate, _second_answer])
    run_fundamentals_analyst(model, state, provider, model=MODEL)
    retry = _prompt(model.calls[1]["messages"])
    assert "calculate" in retry and "<rejected_report>" in retry
    assert "Rs 92,852 crore" in retry  # the one figure that really failed
    failing_line = retry.split("were not found")[1].split("<rejected_report>")[0]
    assert "39,786" not in failing_line and "24,161" not in failing_line  # outflows now pass


# --- the two tolerances ----------------------------------------------------------------


def test_a_negative_shown_value_may_be_written_as_a_positive_amount() -> None:
    assert unverified_crore_figures("An outflow of Rs 39,786 crore.", [-39786.0]) == []
    assert unverified_crore_figures("An outflow of Rs 39,787 crore.", [-39786.0]) == [
        "Rs 39,787 crore"
    ]


def test_a_truncated_copy_passes_but_a_wrong_digit_does_not() -> None:
    assert unverified_crore_figures("Rs 1,211.99 crore", [1211.9955]) == []
    assert unverified_crore_figures("Rs 1,212.00 crore", [1211.9955]) == []
    assert unverified_crore_figures("Rs 1,211.98 crore", [1211.9955]) == ["Rs 1,211.98 crore"]


def test_other_units_stay_unverifiable() -> None:
    assert len(unverified_crore_figures("Rs 2.5 lakh crore and Rs 4 billion", [250000.0])) == 2


# --- the calculator inside the analyst -------------------------------------------------


def test_calculate_is_offered_and_the_prompt_explains_it() -> None:
    provider, state = _infy()
    model = ScriptedModel([lambda m: _text_turn("Neutral, no amounts quoted.")])
    run_fundamentals_analyst(model, state, provider, model=MODEL)
    call = model.calls[0]
    assert [t["name"] for t in call["tools"]] == ["calculate"]
    system = call["system"]
    assert "calculate" in system and "outflow" in system and "Rs <number> crore" in system


def test_a_refused_calculation_is_returned_to_the_model_as_an_error() -> None:
    provider, state = _infy()
    model = ScriptedModel(
        [
            _tool_turn(("c1", "calculate", {"expression": "1230"})),
            lambda m: _text_turn("No derived amounts quoted."),
        ]
    )
    report = run_fundamentals_analyst(model, state, provider, model=MODEL).fundamentals_report
    block = model.calls[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "1230" in block["content"]
    assert report is not None and report.status == "ok"
    assert len(model.calls) == 2  # no retry: nothing was ungrounded


def test_an_invented_amount_is_a_gap_after_exactly_one_retry() -> None:
    provider, state = _infy()
    answer = _text_turn("Cash of Rs 123,456 crore sits on the balance sheet.")
    model = ScriptedModel([answer, answer, answer])
    report = run_fundamentals_analyst(model, state, provider, model=MODEL).fundamentals_report
    assert report is not None and report.status == "gap"
    assert "Rs 123,456 crore" in (report.gap_reason or "") and "first attempt" in (
        report.gap_reason or ""
    )
    assert len(model.calls) == 2  # never a second retry
    assert "Rs 123,456 crore" in _prompt(model.calls[1]["messages"])


def test_other_failures_are_not_retried() -> None:
    provider, state = _infy()
    for turn in (_text_turn("   "), _text_turn("cut off", stop_reason="max_tokens")):
        model = ScriptedModel([turn])
        report = run_fundamentals_analyst(model, state, provider, model=MODEL).fundamentals_report
        assert report is not None and report.status == "gap"
        assert len(model.calls) == 1


def test_a_past_dated_cell_still_makes_no_model_call() -> None:
    cell = next(c for c in _cells() if c.as_of.date().isoformat() == "2026-10-21")
    provider = FrozenDataProvider(FIXTURES)
    model = ScriptedModel([])
    report = run_fundamentals_analyst(
        model, _state(provider, cell.ticker, cell.as_of), provider, model=MODEL
    ).fundamentals_report
    assert model.calls == []
    assert report is not None and report.insufficient_data is True
