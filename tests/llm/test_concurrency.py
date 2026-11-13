"""S2.6: one LLMClient and one trace shared by analysts running in parallel threads."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from helpers import MODEL, make_settings, tool_use_response

from ta_in.llm import CacheMode, LLMClient, ResponseCache, TraceWriter


class BarrierSDK:
    """Every call waits until all callers are inside `create`, so the calls truly overlap.

    The reply is derived from the request, so a mix-up between calls is visible: caller "A" must
    get text "reply-A", a tool call named "tool-A" and 100 + len("A") input tokens back.
    """

    def __init__(self, parties: int) -> None:
        self._barrier = threading.Barrier(parties, timeout=5)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self._barrier.wait()
        who = kwargs["system"]
        return tool_use_response(
            (f"id-{who}", f"tool-{who}", {"who": who}),
            text=f"reply-{who}",
            input_tokens=100 + len(who),
            output_tokens=10 + len(who),
        )


def _events(trace_dir: Path) -> list[dict[str, Any]]:
    (path,) = trace_dir.glob("*.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip()]  # raises if any line is torn


def test_parallel_calls_keep_their_own_trace_lines_and_records(tmp_path: Path) -> None:
    names = ["A", "BB", "CCC", "DDDD"]
    trace = TraceWriter(tmp_path / "traces", "run-1")
    client = LLMClient(
        make_settings(budget=10.0),
        sdk=BarrierSDK(len(names)),
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=CacheMode.OFF,
        trace=trace,
    )
    turns: dict[str, Any] = {}

    def work(who: str) -> None:
        turns[who] = client.complete_turn(
            MODEL, who, [{"role": "user", "content": who}], agent=f"agent-{who}"
        )

    threads = [threading.Thread(target=work, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads)

    calls = [e for e in _events(tmp_path / "traces") if e["type"] == "call"]
    assert len(calls) == len(names)
    for call in calls:
        who = call["agent"].removeprefix("agent-")
        assert call["response"] == f"reply-{who}"
        assert call["tool_calls"] == [
            {"id": f"id-{who}", "name": f"tool-{who}", "input": {"who": who}}
        ]
        assert call["input_tokens"] == 100 + len(who)
        assert call["output_tokens"] == 10 + len(who)
        assert call["cost_usd"] > 0
    for who in names:
        assert turns[who].text == f"reply-{who}"
    assert len(client.records) == len(names)
    assert sorted(r.input_tokens for r in client.records) == sorted(100 + len(n) for n in names)
    assert client.spent_usd == sum(r.cost_usd for r in client.records)
