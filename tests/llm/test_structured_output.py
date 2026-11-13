"""S4.2: structured output through a forced tool, validated, retried once, then a loud failure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import MODEL, FakeSDK, make_settings, response, tool_use_response
from pydantic import BaseModel

from ta_in.llm import (
    BudgetExceeded,
    CacheMode,
    LLMClient,
    ResponseCache,
    StructuredOutputError,
    TraceWriter,
    complete_structured,
    summarize,
)
from ta_in.schemas import PortfolioDecision, PortfolioRating, ResearchPlan, TraderProposal

MSGS = [{"role": "user", "content": "decide"}]
TOOL = "submit_result"


class Pick(BaseModel):
    rating: PortfolioRating
    reason: str


GOOD = {"rating": "Buy", "reason": "strong"}
BAD_ENUM = {"rating": "Maybe", "reason": "unsure"}
MISSING = {"rating": "Hold"}


def good(id_: str = "tu_g") -> Any:
    return tool_use_response((id_, TOOL, GOOD))


def bad(inp: dict[str, Any] = BAD_ENUM, id_: str = "tu_b") -> Any:
    return tool_use_response((id_, TOOL, inp))


def make(
    tmp_path: Path,
    script: list[Any],
    mode: CacheMode = CacheMode.OFF,
    trace: TraceWriter | None = None,
    budget: float = 1.0,
) -> tuple[LLMClient, FakeSDK]:
    sdk = FakeSDK(script)
    client = LLMClient(
        make_settings(budget),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
        trace=trace,
    )
    return client, sdk


def run(client: LLMClient, trace: TraceWriter | None = None) -> Pick:
    return complete_structured(
        client,
        model=MODEL,
        system="sys",
        messages=MSGS,
        output_model=Pick,
        agent="mgr",
        trace=trace,
    )


def events(trace: TraceWriter) -> list[dict[str, Any]]:
    return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]


# --- happy path ------------------------------------------------------------------------


def test_a_valid_first_answer_returns_the_model_in_one_call(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [good()])
    result = run(client)
    assert result == Pick(rating=PortfolioRating.BUY, reason="strong")
    assert len(sdk.calls) == 1
    call = sdk.calls[0]
    assert [t["name"] for t in call["tools"]] == [TOOL]
    assert call["tools"][0]["input_schema"] == Pick.model_json_schema()
    assert call["tool_choice"] == {"type": "tool", "name": TOOL}
    assert call["messages"] == MSGS


# --- criterion 1: a malformed response triggers exactly one retry ----------------------


@pytest.mark.parametrize(
    "first",
    [
        bad(BAD_ENUM),
        bad(MISSING),
        response("I would buy it."),  # text only, no tool call
        tool_use_response(("tu_x", "other_tool", GOOD)),  # wrong tool
        tool_use_response(("tu_1", TOOL, GOOD), ("tu_2", TOOL, GOOD)),  # two calls
        tool_use_response(("tu_c", TOOL, {"rating": "Bu"}), stop_reason="max_tokens"),  # cut off
    ],
    ids=["bad-enum", "missing-field", "text-only", "wrong-tool", "two-calls", "cut-off"],
)
def test_a_malformed_response_is_retried_exactly_once(tmp_path: Path, first: Any) -> None:
    client, sdk = make(tmp_path, [first, good()])
    assert run(client) == Pick(rating=PortfolioRating.BUY, reason="strong")
    assert len(sdk.calls) == 2
    assert sdk.script == []


def test_the_retry_shows_the_model_its_answer_and_the_validation_error(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [bad(BAD_ENUM, "tu_b"), good()])
    run(client)

    retry = sdk.calls[1]["messages"]
    assert retry[0] == MSGS[0]
    assert retry[1]["role"] == "assistant"
    assert retry[1]["content"][0]["id"] == "tu_b"
    answer = retry[2]
    assert answer["role"] == "user"
    block = answer["content"][0]
    assert block["type"] == "tool_result" and block["tool_use_id"] == "tu_b"
    assert block["is_error"] is True
    assert "rating" in block["content"]  # names the field that failed
    assert sdk.calls[1]["tool_choice"] == {"type": "tool", "name": TOOL}


def test_a_text_only_retry_names_the_required_tool(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [response("I would buy it."), good()])
    run(client)
    retry = sdk.calls[1]["messages"]
    assert retry[1]["role"] == "assistant"
    assert retry[2]["role"] == "user"
    assert TOOL in json.dumps(retry[2]["content"])


def test_every_tool_use_of_a_bad_turn_is_answered(tmp_path: Path) -> None:
    two = tool_use_response(("tu_1", TOOL, GOOD), ("tu_2", TOOL, GOOD))
    client, sdk = make(tmp_path, [two, good()])
    run(client)
    ids = [b["tool_use_id"] for b in sdk.calls[1]["messages"][2]["content"]]
    assert ids == ["tu_1", "tu_2"]


# --- criterion 2: a second failure raises, raw output saved to the trace ---------------


def test_a_second_failure_raises_after_exactly_two_calls(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [bad(BAD_ENUM), bad(MISSING)])
    with pytest.raises(StructuredOutputError) as exc:
        run(client)
    assert len(sdk.calls) == 2  # never a third
    assert [a.raw for a in exc.value.attempts] == [BAD_ENUM, MISSING]
    assert "rating" in str(exc.value)


def test_the_raw_output_of_both_attempts_is_saved_to_the_trace(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "traces", run_id="run1")
    client, _ = make(tmp_path, [bad(BAD_ENUM), response("not a tool call")], trace=trace)
    with pytest.raises(StructuredOutputError):
        run(client, trace)

    failure = [e for e in events(trace) if e["type"] == "structured_failure"]
    assert len(failure) == 1
    attempts = failure[0]["attempts"]
    assert failure[0]["agent"] == "mgr"
    assert attempts[0]["raw"] == BAD_ENUM
    assert attempts[1]["raw"] == "not a tool call"
    assert all(a["error"] for a in attempts)
    assert len([e for e in events(trace) if e["type"] == "call"]) == 2


def test_summarize_ignores_the_failure_event(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "traces", run_id="run2")
    client, _ = make(tmp_path, [bad(), bad()], trace=trace)
    with pytest.raises(StructuredOutputError):
        run(client, trace)
    summary = summarize(trace.path)
    assert summary.total.calls == 2
    assert summary.total.cost_usd > 0


def test_without_a_trace_a_failure_still_raises(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [bad(), bad()])
    with pytest.raises(StructuredOutputError):
        run(client)


# --- other errors, cost, replay --------------------------------------------------------


def test_budget_errors_are_not_retried(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [bad(), good()], budget=0.0001)
    with pytest.raises(BudgetExceeded):
        run(client)
    assert len(sdk.calls) == 1


def test_both_attempts_are_costed(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [bad(), good()])
    run(client)
    assert len(client.records) == 2


def test_a_retried_run_replays_with_no_sdk_calls(tmp_path: Path) -> None:
    rec, _ = make(tmp_path, [bad(), good()], mode=CacheMode.RECORD)
    recorded = run(rec)

    rep, sdk = make(tmp_path, [], mode=CacheMode.REPLAY)
    assert run(rep) == recorded
    assert sdk.calls == []


# --- the real decision schemas ---------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ResearchPlan, {"recommendation": "Hold", "rationale": "r", "strategic_actions": "a"}),
        (TraderProposal, {"action": "Buy", "reasoning": "r"}),
        (PortfolioDecision, {"rating": "Sell", "executive_summary": "s", "investment_thesis": "t"}),
    ],
)
def test_the_real_decision_models_work_through_it(
    tmp_path: Path, model: type[BaseModel], payload: dict[str, Any]
) -> None:
    client, sdk = make(tmp_path, [tool_use_response(("tu", TOOL, payload))])
    out = complete_structured(
        client, model=MODEL, system="s", messages=MSGS, output_model=model, agent="a"
    )
    assert isinstance(out, model)
    dumped = out.model_dump(mode="json")
    assert all(dumped[key] == value for key, value in payload.items())
    assert len(sdk.calls) == 1
