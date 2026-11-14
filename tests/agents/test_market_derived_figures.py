"""Derived price figures: the calculator, judgement labels and the one retry (offline).

Background: the first real runs (2026-11-13) lost the market analyst because the strict price check
rejected a correct derived figure ("Rs 47", the gap to a moving average) and an unshown level
("Rs 1230"). See PROGRESS.md.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_market_analyst import (
    AS_OF,
    MODEL,
    LeakyProvider,
    ScriptedModel,
    _any_state,
    _call,
    _snap,
    _text_turn,
    _tool_results,
    _tool_turn,
    _toolkit,
)

from ta_in.agents import run_market_analyst, unverified_price_figures
from ta_in.agents.market_tools import MarketToolkit
from ta_in.llm import LLMTurn
from ta_in.state import AgentState


def _snapshot_close(state: AgentState) -> float:
    assert state.price_snapshot is not None
    return state.price_snapshot.last_close


def _derive(messages: list[dict[str, Any]]) -> LLMTurn:
    bars = _tool_results(messages)[-1]["bars"]
    a, b = bars[-1]["close"], bars[-2]["close"]
    return _tool_turn(("c1", "calculate", {"expression": f"{a} - {b}"}))


def _report_with_the_calculated_value(messages: list[dict[str, Any]]) -> LLMTurn:
    value = _tool_results(messages)[-1]["value"]
    return _text_turn(f"Closed above the previous close by Rs {value:.2f}.")


# --- the calculate tool ----------------------------------------------------------------


def test_calculate_is_the_third_tool() -> None:
    names = [t.name for t in _toolkit(AS_OF).tools()]
    assert names == ["get_price_history", "get_indicator", "calculate"]


def test_a_calculated_result_is_remembered_so_the_report_may_quote_it() -> None:
    kit = _toolkit(AS_OF)
    out = _call(kit, "get_price_history", {"days": 3})
    a, b = out["bars"][-1]["close"], out["bars"][-2]["close"]
    result = _call(kit, "calculate", {"expression": f"{a} - {b}"})
    assert result["value"] == pytest.approx(a - b, abs=1e-4)
    assert result["value"] in kit.seen_values
    assert unverified_price_figures(f"Up Rs {a - b:.2f}", _snap(), kit.seen_values) == []


def test_a_negative_calculated_gap_may_be_reported_as_a_positive_figure() -> None:
    # Real INFY run: calculate("1141.80 - 1188.9775") returned -47.1775; the report said Rs 47.18.
    kit = _toolkit(AS_OF)
    out = _call(kit, "get_price_history", {"days": 3})
    a, b = out["bars"][-1]["close"], out["bars"][-2]["close"]
    result = _call(kit, "calculate", {"expression": f"{b} - {a}"})  # negative: previous - last
    assert result["value"] < 0
    figure = abs(result["value"])
    assert unverified_price_figures(f"Down Rs {figure:.2f}", _snap(), kit.seen_values) == []
    assert unverified_price_figures(f"Down Rs {figure + 1:.2f}", _snap(), kit.seen_values) != []


def test_a_truncated_copy_of_a_tool_value_passes_but_a_wrong_digit_does_not() -> None:
    # Real INFY run: the tool showed 1211.9955 and the report said Rs 1211.99.
    assert unverified_price_figures("It fell from Rs 1211.99", _snap(), [1211.9955]) == []
    assert unverified_price_figures("It fell from Rs 1212.00", _snap(), [1211.9955]) == []
    assert unverified_price_figures("It fell from Rs 1211.98", _snap(), [1211.9955]) != []


def test_calculate_accepts_snapshot_prices_as_operands() -> None:
    kit = MarketToolkit(
        LeakyProvider(AS_OF),  # type: ignore[arg-type]
        "AAA.NS",
        AS_OF,
        snapshot=_snap(),
    )
    result = _call(kit, "calculate", {"expression": "2529.0 - 1982.6"})
    assert result["value"] == pytest.approx(546.4)
    with pytest.raises(Exception, match="1230"):
        _call(kit, "calculate", {"expression": "1230 - 2529.0"})


def test_calculate_cannot_launder_a_made_up_number() -> None:
    kit = _toolkit(AS_OF)
    with pytest.raises(Exception, match="1230"):
        _call(kit, "calculate", {"expression": "1230"})
    assert 1230.0 not in kit.seen_values


def test_calculate_is_capped_per_run() -> None:
    kit = _toolkit(AS_OF)
    out = _call(kit, "get_price_history", {"days": 2})
    a = out["bars"][-1]["close"]
    for _ in range(30):
        _call(kit, "calculate", {"expression": f"{a} * 2"})
    with pytest.raises(Exception, match="at most 30"):
        _call(kit, "calculate", {"expression": f"{a} * 2"})


def test_the_loop_returns_a_refused_calculation_to_the_model_as_an_error() -> None:
    provider, state = _any_state()
    model = ScriptedModel(
        [
            _tool_turn(("t1", "calculate", {"expression": "1230"})),
            lambda m: _text_turn("No derived level quoted."),
        ]
    )
    new = run_market_analyst(model, state, provider, model=MODEL)
    block = model.calls[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "1230" in block["content"]
    assert new.market_report is not None and new.market_report.status == "ok"


# --- the analyst with derived figures --------------------------------------------------


def test_a_derived_figure_from_the_calculator_makes_the_report_pass() -> None:
    provider, state = _any_state()
    model = ScriptedModel(
        [
            _tool_turn(("t1", "get_price_history", {"days": 5})),
            _derive,
            _report_with_the_calculated_value,
        ]
    )
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None and report.status == "ok", report and report.gap_reason
    assert len(model.calls) == 3  # no retry was needed


def test_a_derived_figure_without_the_calculator_is_retried_once_and_then_accepted() -> None:
    provider, state = _any_state()
    close = _snapshot_close(state)
    model = ScriptedModel(
        [
            _text_turn(f"Closed at Rs {close:.2f}, about Rs 47 under the average."),
            _tool_turn(("t1", "get_price_history", {"days": 5})),
            _derive,
            _report_with_the_calculated_value,
        ]
    )
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None and report.status == "ok", report and report.gap_reason
    retry_prompt = model.calls[1]["messages"][0]["content"]
    assert "Rs 47" in retry_prompt and "calculate" in retry_prompt
    assert "about Rs 47 under the average" in retry_prompt  # the rejected report is shown
    assert len(model.calls) == 4  # the failed report, then the retry run's three model calls


def test_the_unshown_level_case_from_the_real_run_is_a_gap_after_exactly_one_retry() -> None:
    provider, state = _any_state()
    answer = _text_turn("Resistance sits at Rs 1230, above the last close.")
    model = ScriptedModel([answer, answer, answer])
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None and report.status == "gap"
    assert "Rs 1230" in (report.gap_reason or "")
    assert len(model.calls) == 2  # never a second retry


def test_other_failures_are_not_retried() -> None:
    provider, state = _any_state()
    for turn in (_text_turn("  "), _text_turn("cut off", stop_reason="max_tokens")):
        model = ScriptedModel([turn])
        report = run_market_analyst(model, state, provider, model=MODEL).market_report
        assert report is not None and report.status == "gap"
        assert len(model.calls) == 1


# --- judgement levels ------------------------------------------------------------------


def test_a_judgement_level_passes_and_is_labelled_as_unverified() -> None:
    provider, state = _any_state()
    close = _snapshot_close(state)
    text = (
        f"Closed at Rs {close:.2f}. <judgement>Support near Rs 1,100 and resistance near "
        f"Rs 1,300</judgement> look relevant."
    )
    model = ScriptedModel([_text_turn(text)])
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None and report.status == "ok", report and report.gap_reason
    assert "<judgement>" not in report.text and "</judgement>" not in report.text
    label = "[analyst judgement, unverified: Support near Rs 1,100 and resistance near Rs 1,300]"
    assert label in report.text
    assert f"Rs {close:.2f}" in report.text
    assert len(model.calls) == 1


def test_a_bare_unverified_figure_next_to_a_judgement_tag_is_still_rejected() -> None:
    provider, state = _any_state()
    text = "Target Rs 1500 is likely. <judgement>Support near Rs 1,100</judgement>."
    model = ScriptedModel([_text_turn(text), _text_turn(text)])
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None and report.status == "gap"
    assert "Rs 1500" in (report.gap_reason or "") and "Rs 1,100" not in (report.gap_reason or "")


@pytest.mark.parametrize(
    "text",
    [
        "Level <judgement>Support near Rs 1,100 and no closing tag.",
        "Level <judgement>outer <judgement>inner</judgement></judgement>.",
        "Level </judgement> stray closing tag.",
        " ".join(f"<judgement>level {i}</judgement>" for i in range(6)),
    ],
    ids=["unclosed", "nested", "stray-close", "six-tags"],
)
def test_malformed_or_excessive_judgement_tags_are_a_gap_without_a_retry(text: str) -> None:
    provider, state = _any_state()
    model = ScriptedModel([_text_turn(text), _text_turn(text)])
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None and report.status == "gap"
    assert "judgement" in (report.gap_reason or "")
    assert len(model.calls) == 1


def test_five_judgement_tags_are_allowed() -> None:
    provider, state = _any_state()
    text = " ".join(f"<judgement>level {i} is near Rs {1000 + i}</judgement>" for i in range(5))
    report = run_market_analyst(
        ScriptedModel([_text_turn(text)]), state, provider, model=MODEL
    ).market_report
    assert report is not None and report.status == "ok", report and report.gap_reason
    assert report.text.count("analyst judgement, unverified") == 5


def test_the_prompt_explains_calculate_and_the_judgement_label() -> None:
    provider, state = _any_state()
    model = ScriptedModel([_text_turn("No price levels to quote here.")])
    run_market_analyst(model, state, provider, model=MODEL)
    system = model.calls[0]["system"]
    assert "calculate" in system and "<judgement>" in system and "unverified" in system
    assert "Rs" in system and "8" in system
