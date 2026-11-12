"""Tool-aware turns: complete_turn parses, caches, traces and replays tool_use blocks."""

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import MODEL, FakeSDK, make_settings, response, tool_use_response
from pydantic import BaseModel

from ta_in.agents import Tool, run_agent
from ta_in.llm import CacheMode, LLMClient, LLMTurn, ResponseCache, TraceWriter
from ta_in.state import AgentName

MSGS = [{"role": "user", "content": "hi"}]
TOOLS = [{"name": "add", "description": "d", "input_schema": {"type": "object"}}]


def make_client(
    tmp_path: Path,
    mode: CacheMode,
    script: list[Any] | None = None,
    trace: TraceWriter | None = None,
) -> tuple[LLMClient, FakeSDK]:
    sdk = FakeSDK(script or [])
    client = LLMClient(
        make_settings(),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
        trace=trace,
    )
    return client, sdk


def test_complete_turn_parses_text_tool_use_and_stop_reason(tmp_path: Path) -> None:
    client, sdk = make_client(
        tmp_path,
        CacheMode.OFF,
        [tool_use_response(("tu_1", "add", {"a": 1, "b": 2}), text="let me add")],
    )
    turn = client.complete_turn(MODEL, "sys", MSGS, tools=TOOLS)
    assert turn.stop_reason == "tool_use"
    assert turn.text == "let me add"
    assert [(u.id, u.name, u.input) for u in turn.tool_uses] == [("tu_1", "add", {"a": 1, "b": 2})]
    assert turn.content[0] == {"type": "text", "text": "let me add"}
    assert turn.content[1] == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "add",
        "input": {"a": 1, "b": 2},
    }
    assert sdk.calls[0]["tools"] == TOOLS


def test_complete_still_returns_text_for_the_same_response(tmp_path: Path) -> None:
    client, _ = make_client(
        tmp_path, CacheMode.OFF, [tool_use_response(("tu_1", "add", {}), text="hello")]
    )
    assert client.complete(MODEL, "sys", MSGS, tools=TOOLS) == "hello"


def test_a_plain_text_response_without_stop_reason_is_end_turn(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, CacheMode.OFF, [response("done")])
    turn = client.complete_turn(MODEL, "sys", MSGS)
    assert turn.stop_reason == "end_turn"
    assert turn.text == "done"
    assert turn.tool_uses == []


def test_record_then_replay_returns_an_identical_turn_without_the_sdk(tmp_path: Path) -> None:
    rec, _ = make_client(
        tmp_path,
        CacheMode.RECORD,
        [tool_use_response(("tu_1", "add", {"a": 1, "b": 2}), text="adding")],
    )
    recorded = rec.complete_turn(MODEL, "sys", MSGS, tools=TOOLS)

    rep, sdk = make_client(tmp_path, CacheMode.REPLAY, [])
    replayed = rep.complete_turn(MODEL, "sys", MSGS, tools=TOOLS)
    assert replayed == recorded
    assert sdk.calls == []
    assert rep.records[0].cached is True
    assert rep.spent_usd == 0.0


def test_a_cache_file_without_content_still_replays_as_text(tmp_path: Path) -> None:
    rec, _ = make_client(tmp_path, CacheMode.RECORD, [response("old style")])
    rec.complete(MODEL, "sys", MSGS)
    (path,) = (tmp_path / "cache").glob("*.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("content", None)
    data.pop("stop_reason", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    rep, _ = make_client(tmp_path, CacheMode.REPLAY)
    turn = rep.complete_turn(MODEL, "sys", MSGS)
    assert turn.text == "old style"
    assert turn.stop_reason == "end_turn"
    assert rep.complete(MODEL, "sys", MSGS) == "old style"


def test_the_trace_records_the_tool_use_blocks(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "traces", "run-1")
    client, _ = make_client(
        tmp_path, CacheMode.OFF, [tool_use_response(("tu_9", "add", {"a": 5}))], trace=trace
    )
    client.complete_turn(MODEL, "sys", MSGS, tools=TOOLS, agent="market_analyst")
    events = [
        json.loads(x)
        for x in next((tmp_path / "traces").glob("*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
        if x.strip()
    ]
    (line,) = [e for e in events if e["type"] == "call"]
    assert line["agent"] == "market_analyst"
    assert line["tool_calls"] == [{"id": "tu_9", "name": "add", "input": {"a": 5}}]


def test_an_llm_turn_is_frozen_and_json_safe() -> None:
    turn = LLMTurn(content=[{"type": "text", "text": "x"}], stop_reason="end_turn")
    assert LLMTurn.model_validate_json(turn.model_dump_json()) == turn
    with pytest.raises(ValueError):
        turn.stop_reason = "other"  # type: ignore[misc]


# --- the whole loop through the real client: record once, replay with no SDK -------------


class AddInput(BaseModel):
    a: int
    b: int


def _add(args: AddInput) -> dict[str, int]:
    return {"sum": args.a + args.b}


def test_a_recorded_agent_run_replays_identically_with_zero_sdk_calls(tmp_path: Path) -> None:
    tool = Tool(name="add", description="Add two numbers", input_model=AddInput, fn=_add)
    script = [
        tool_use_response(("tu_1", "add", {"a": 1, "b": 2}), text="first"),
        tool_use_response(("tu_2", "add", {"a": 3, "b": 4})),
        response("the sums are 3 and 7"),
    ]
    rec, sdk = make_client(tmp_path, CacheMode.RECORD, script)
    first = run_agent(
        rec,
        agent=AgentName.MARKET_ANALYST,
        model=MODEL,
        system="sys",
        prompt="add things",
        tools=[tool],
    )
    assert len(sdk.calls) == 3
    assert first.stopped_by == "end_turn"

    rep, sdk2 = make_client(tmp_path, CacheMode.REPLAY)
    second = run_agent(
        rep,
        agent=AgentName.MARKET_ANALYST,
        model=MODEL,
        system="sys",
        prompt="add things",
        tools=[tool],
    )
    assert sdk2.calls == []
    assert second == first
    assert second.text == "the sums are 3 and 7"
