"""S2.2: the generic agent loop, driven by a scripted fake model (no SDK, no network)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import BaseModel

from ta_in.agents import AgentResult, Tool, run_agent
from ta_in.data import PointInTimeError
from ta_in.llm import BudgetExceeded, LLMTurn
from ta_in.state import AgentName

MODEL = "claude-sonnet-5-5"


class AddInput(BaseModel):
    a: int
    b: int


class Quote(BaseModel):
    symbol: str
    price: float


def _tool_turn(*uses: tuple[str, str, dict[str, Any]], text: str = "") -> LLMTurn:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in uses]
    return LLMTurn(content=content, stop_reason="tool_use")


def _text_turn(text: str, stop_reason: str = "end_turn") -> LLMTurn:
    return LLMTurn(content=[{"type": "text", "text": text}], stop_reason=stop_reason)


class ScriptedModel:
    """Stands in for LLMClient: returns prepared turns and records every request."""

    def __init__(self, script: list[LLMTurn | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete_turn(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 16000,
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
    ) -> LLMTurn:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": copy.deepcopy(messages),
                "max_tokens": max_tokens,
                "tools": tools,
                "agent": agent,
            }
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _add(args: AddInput) -> dict[str, int]:
    return {"sum": args.a + args.b}


ADD = Tool(name="add", description="Add two integers", input_model=AddInput, fn=_add)


def _run(model: ScriptedModel, tools: list[Tool] | None = None, **kw: Any) -> AgentResult:
    return run_agent(
        model,
        agent=AgentName.MARKET_ANALYST,
        model=MODEL,
        system="you are an analyst",
        prompt="start",
        tools=[ADD] if tools is None else tools,
        **kw,
    )


def _results(call: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool_result blocks of the last (user) message sent in a recorded request."""
    last = call["messages"][-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    return list(last["content"])


# --- criterion 1: a scripted fake model covers multi-step tool use ----------------------


def test_a_model_that_answers_at_once_ends_after_one_step() -> None:
    model = ScriptedModel([_text_turn("hello")])
    out = _run(model)
    assert (out.text, out.steps, out.stopped_by) == ("hello", 1, "end_turn")
    assert out.tool_calls == []
    assert model.calls[0]["messages"] == [{"role": "user", "content": "start"}]


def test_a_three_step_run_feeds_each_tool_result_back_to_the_model() -> None:
    model = ScriptedModel(
        [
            _tool_turn(("t1", "add", {"a": 1, "b": 2}), text="adding"),
            _tool_turn(("t2", "add", {"a": 3, "b": 4})),
            _text_turn("3 and 7"),
        ]
    )
    out = _run(model)

    assert (out.text, out.steps, out.stopped_by) == ("3 and 7", 3, "end_turn")
    assert [c.name for c in out.tool_calls] == ["add", "add"]
    assert [c.is_error for c in out.tool_calls] == [False, False]
    assert len(model.calls) == 3

    second = model.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"][0] == {"type": "text", "text": "adding"}
    assert second[1]["content"][1]["id"] == "t1"
    (result,) = _results(model.calls[1])
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "t1"
    assert json.loads(result["content"]) == {"sum": 3}
    assert "is_error" not in result

    (result2,) = _results(model.calls[2])
    assert json.loads(result2["content"]) == {"sum": 7}
    assert out.messages[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "3 and 7"}],
    }


def test_two_tool_calls_in_one_turn_are_answered_in_one_message_in_order() -> None:
    model = ScriptedModel(
        [
            _tool_turn(("a", "add", {"a": 1, "b": 1}), ("b", "add", {"a": 2, "b": 2})),
            _text_turn("done"),
        ]
    )
    _run(model)
    results = _results(model.calls[1])
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert [json.loads(r["content"])["sum"] for r in results] == [2, 4]


def test_the_request_carries_the_system_prompt_tool_schemas_and_agent_label() -> None:
    model = ScriptedModel([_text_turn("ok")])
    _run(model, max_tokens=123)
    call = model.calls[0]
    assert call["system"] == "you are an analyst"
    assert call["model"] == MODEL
    assert call["agent"] == "market_analyst"
    assert call["max_tokens"] == 123
    (schema,) = call["tools"]
    assert schema["name"] == "add"
    assert schema["description"] == "Add two integers"
    assert set(schema["input_schema"]["properties"]) == {"a", "b"}


def test_with_no_tools_none_is_sent() -> None:
    model = ScriptedModel([_text_turn("ok")])
    _run(model, tools=[])
    assert model.calls[0]["tools"] is None


def test_a_tool_may_return_a_pydantic_model_or_a_plain_string() -> None:
    quote = Tool(
        name="quote",
        description="q",
        input_model=Quote,
        fn=lambda a: Quote(symbol=a.symbol, price=a.price * 2),
    )
    echo = Tool(name="echo", description="e", input_model=AddInput, fn=lambda a: "plain text")
    model = ScriptedModel(
        [
            _tool_turn(
                ("q", "quote", {"symbol": "TCS", "price": 2.5}), ("e", "echo", {"a": 1, "b": 1})
            ),
            _text_turn("ok"),
        ]
    )
    _run(model, tools=[quote, echo])
    q, e = _results(model.calls[1])
    assert json.loads(q["content"]) == {"symbol": "TCS", "price": 5.0}
    assert e["content"] == "plain text"


# --- criterion 2: a tool error goes back to the model as a structured error -------------


def _error(result: dict[str, Any]) -> dict[str, Any]:
    assert result["is_error"] is True
    body = json.loads(result["content"])
    assert set(body) == {"error"}
    return dict(body["error"])


def test_a_tool_exception_becomes_a_structured_error_and_the_loop_goes_on() -> None:
    def boom(args: AddInput) -> dict[str, int]:
        raise ValueError("no data for that date")

    bad = Tool(name="add", description="d", input_model=AddInput, fn=boom)
    model = ScriptedModel([_tool_turn(("t1", "add", {"a": 1, "b": 2})), _text_turn("sorry")])
    out = _run(model, tools=[bad])

    err = _error(_results(model.calls[1])[0])
    assert err == {"type": "ValueError", "message": "no data for that date"}
    assert out.text == "sorry"
    assert out.stopped_by == "end_turn"
    assert [c.is_error for c in out.tool_calls] == [True]


def test_an_unknown_tool_is_a_structured_error() -> None:
    model = ScriptedModel([_tool_turn(("t1", "nope", {})), _text_turn("ok")])
    _run(model)
    err = _error(_results(model.calls[1])[0])
    assert err["type"] == "UnknownTool"
    assert "nope" in err["message"]
    assert "add" in err["message"]  # tells the model what does exist


def test_input_that_fails_validation_is_a_structured_error_and_the_tool_is_not_called() -> None:
    called: list[AddInput] = []

    def spy(args: AddInput) -> dict[str, int]:
        called.append(args)
        return {"sum": 0}

    tool = Tool(name="add", description="d", input_model=AddInput, fn=spy)
    model = ScriptedModel([_tool_turn(("t1", "add", {"a": "x"})), _text_turn("ok")])
    _run(model, tools=[tool])
    err = _error(_results(model.calls[1])[0])
    assert err["type"] == "InvalidToolInput"
    assert called == []


def test_one_failing_call_does_not_hide_a_successful_one_in_the_same_turn() -> None:
    model = ScriptedModel(
        [
            _tool_turn(("ok", "add", {"a": 1, "b": 1}), ("bad", "add", {"a": "x"})),
            _text_turn("done"),
        ]
    )
    out = _run(model)
    good, bad = _results(model.calls[1])
    assert json.loads(good["content"]) == {"sum": 2}
    assert bad["is_error"] is True
    assert [c.is_error for c in out.tool_calls] == [False, True]


def test_a_look_ahead_leak_is_not_swallowed() -> None:
    def leak(args: AddInput) -> dict[str, int]:
        raise PointInTimeError("bar dated after as_of")

    tool = Tool(name="add", description="d", input_model=AddInput, fn=leak)
    model = ScriptedModel([_tool_turn(("t1", "add", {"a": 1, "b": 1}))])
    with pytest.raises(PointInTimeError):
        _run(model, tools=[tool])


def test_a_budget_stop_propagates() -> None:
    model = ScriptedModel([BudgetExceeded("over budget")])
    with pytest.raises(BudgetExceeded):
        _run(model)


# --- criterion 3: hitting the step limit stops cleanly ----------------------------------


def test_the_step_limit_ends_the_run_without_an_exception() -> None:
    model = ScriptedModel([_tool_turn((f"t{i}", "add", {"a": i, "b": 1})) for i in range(10)])
    out = _run(model, max_steps=3)

    assert out.stopped_by == "step_limit"
    assert out.steps == 3
    assert len(model.calls) == 3  # exactly max_steps model calls, never more
    assert len(out.tool_calls) == 3
    assert out.messages[-1]["role"] == "user"  # the last tool results were still recorded


def test_a_model_that_finishes_on_the_last_allowed_step_is_not_a_step_limit() -> None:
    model = ScriptedModel([_tool_turn(("t1", "add", {"a": 1, "b": 1})), _text_turn("done")])
    out = _run(model, max_steps=2)
    assert (out.stopped_by, out.steps) == ("end_turn", 2)


def test_the_text_of_a_step_limited_run_is_the_last_thing_the_model_said() -> None:
    model = ScriptedModel(
        [
            _tool_turn(("t1", "add", {"a": 1, "b": 1}), text="working on it"),
            _tool_turn(("t2", "add", {"a": 1, "b": 1})),
        ]
    )
    out = _run(model, max_steps=2)
    assert out.stopped_by == "step_limit"
    assert out.text == "working on it"


# --- other stop reasons and input checks ------------------------------------------------


def test_a_max_tokens_stop_ends_the_loop_and_is_reported() -> None:
    model = ScriptedModel([_text_turn("cut off", stop_reason="max_tokens")])
    out = _run(model)
    assert (out.stopped_by, out.text) == ("max_tokens", "cut off")


def test_max_steps_must_be_at_least_one() -> None:
    with pytest.raises(ValueError):
        _run(ScriptedModel([]), max_steps=0)


def test_duplicate_tool_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _run(ScriptedModel([]), tools=[ADD, ADD])


def test_the_result_is_frozen() -> None:
    out = _run(ScriptedModel([_text_turn("x")]))
    with pytest.raises(ValueError):
        out.text = "y"  # type: ignore[misc]
