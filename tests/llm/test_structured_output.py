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
    assert "tool_choice" not in call  # current models reject a forced tool choice
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
    assert "tool_choice" not in sdk.calls[1]


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


# --- optional semantic check (S3.2) ----------------------------------------------------


def no_buy(pick: Pick) -> str | None:
    return "Buy is not allowed here" if pick.rating is PortfolioRating.BUY else None


def run_checked(client: LLMClient, trace: TraceWriter | None = None) -> Pick:
    return complete_structured(
        client,
        model=MODEL,
        system="sys",
        messages=MSGS,
        output_model=Pick,
        agent="mgr",
        trace=trace,
        check=no_buy,
    )


def sell(id_: str = "tu_s") -> Any:
    return tool_use_response((id_, TOOL, {"rating": "Sell", "reason": "x"}))


def test_a_passing_check_returns_at_once(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [sell()])
    assert run_checked(client).rating is PortfolioRating.SELL
    assert len(sdk.calls) == 1


def test_a_failing_check_uses_the_one_retry_and_shows_the_model_the_message(
    tmp_path: Path,
) -> None:
    client, sdk = make(tmp_path, [good("tu_g"), sell()])  # GOOD is a Buy: the check rejects it
    assert run_checked(client).rating is PortfolioRating.SELL
    assert len(sdk.calls) == 2
    block = sdk.calls[1]["messages"][2]["content"][0]
    assert block["is_error"] is True
    assert "Buy is not allowed here" in block["content"]


def test_two_failed_checks_raise_and_trace_the_raw_output(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "traces", run_id="run3")
    client, sdk = make(tmp_path, [good("a"), good("b")], trace=trace)
    with pytest.raises(StructuredOutputError) as exc:
        run_checked(client, trace)
    assert len(sdk.calls) == 2
    assert [a.raw for a in exc.value.attempts] == [GOOD, GOOD]
    failure = [e for e in events(trace) if e["type"] == "structured_failure"]
    assert failure[0]["attempts"][0]["error"] == "Buy is not allowed here"


def test_without_a_check_a_buy_is_fine(tmp_path: Path) -> None:
    client, sdk = make(tmp_path, [good()])
    assert run(client).rating is PortfolioRating.BUY
    assert len(sdk.calls) == 1


# --- no forced tool choice: found by the first real run (2026-11-13) --------------------


def test_the_strict_fake_rejects_a_forced_tool_choice_like_the_real_api(tmp_path: Path) -> None:
    client, _ = make(tmp_path, [good()])
    forced = {"type": "tool", "name": TOOL}
    with pytest.raises(AssertionError, match="reject"):
        client.complete_turn(MODEL, "sys", MSGS, tools=[], tool_choice=forced)
    for choice in ({"type": "any"}, {"type": "tool", "name": "x"}):
        with pytest.raises(AssertionError):
            make(tmp_path, [good()])[0].complete_turn(MODEL, "s", MSGS, tool_choice=choice)


def test_a_model_that_answers_in_text_first_gets_the_one_retry_and_then_succeeds(
    tmp_path: Path,
) -> None:
    # Without a forced tool a model may talk instead of calling the tool; the retry covers it.
    client, sdk = make(tmp_path, [response("I think Buy, because it is strong."), good()])
    assert run(client) == Pick(rating=PortfolioRating.BUY, reason="strong")
    assert len(sdk.calls) == 2
    assert all("tool_choice" not in c for c in sdk.calls)
    assert TOOL in json.dumps(sdk.calls[1]["messages"][2]["content"])


def test_a_model_that_writes_text_and_then_calls_the_tool_is_accepted_at_once(
    tmp_path: Path,
) -> None:
    both = tool_use_response(("tu", TOOL, GOOD), text="Here is my answer.")
    client, sdk = make(tmp_path, [both])
    assert run(client) == Pick(rating=PortfolioRating.BUY, reason="strong")
    assert len(sdk.calls) == 1
