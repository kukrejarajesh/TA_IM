import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import FAKE_KEY, MODEL, FakeSDK, make_settings, response

from ta_in.llm import (
    BudgetExceeded,
    CacheMode,
    LLMClient,
    ResponseCache,
    TraceWriter,
    main,
    new_run_id,
    summarize,
)

MSGS = [{"role": "user", "content": "hi"}]


def fake_clock(step: float = 0.25) -> Callable[[], float]:
    t = {"now": 0.0}

    def clock() -> float:
        t["now"] += step
        return t["now"]

    return clock


def make_client(
    tmp_path: Path,
    script: list[Any],
    budget: float = 1.0,
    cache_mode: CacheMode = CacheMode.OFF,
    trace: TraceWriter | None = None,
) -> tuple[LLMClient, TraceWriter]:
    trace = trace or TraceWriter(tmp_path / "traces")
    client = LLMClient(
        make_settings(budget),
        sdk=FakeSDK(script),
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=cache_mode,
        trace=trace,
        clock=fake_clock(),
    )
    return client, trace


def lines(trace: TraceWriter) -> list[dict[str, Any]]:
    return [json.loads(x) for x in trace.path.read_text(encoding="utf-8").splitlines()]


# ---- one file per run ---------------------------------------------------


def test_run_ids_are_unique_and_sortable() -> None:
    a, b = new_run_id(), new_run_id()
    assert a != b
    assert len(a.split("-")) == 3


def test_one_run_writes_exactly_one_file(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [response(), response(), response()])
    for _ in range(3):
        client.complete(MODEL, "sys", MSGS, agent="analyst")
    files = list((tmp_path / "traces").glob("*"))
    assert files == [trace.path]
    assert trace.path.suffix == ".jsonl"
    assert len(lines(trace)) == 1 + 3  # run_start + three calls


def test_two_runs_write_two_files(tmp_path: Path) -> None:
    make_client(tmp_path, [])
    make_client(tmp_path, [])
    assert len(list((tmp_path / "traces").glob("*.jsonl"))) == 2


def test_run_with_no_calls_still_has_its_one_file(tmp_path: Path) -> None:
    _, trace = make_client(tmp_path, [])
    (start,) = lines(trace)
    assert start["type"] == "run_start"
    assert start["run_id"] == trace.run_id


# ---- line content -------------------------------------------------------


def test_call_line_has_all_fields(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [response("hello", 1000, 500)])
    client.complete(MODEL, "be brief", MSGS, agent="trader")
    call = lines(trace)[1]
    assert call["type"] == "call"
    assert call["agent"] == "trader"
    assert call["model"] == MODEL
    assert call["prompt"] == {"system": "be brief", "messages": MSGS}
    assert call["response"] == "hello"
    assert call["tool_calls"] == []
    assert (call["input_tokens"], call["output_tokens"]) == (1000, 500)
    assert call["cost_usd"] == pytest.approx(0.007)
    assert call["latency_s"] == pytest.approx(0.25)
    assert call["cached"] is False
    assert call["attempts"] == 1
    assert call["error"] is None
    assert "ts" in call


def test_default_agent_name(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [response()])
    client.complete(MODEL, "sys", MSGS)
    assert lines(trace)[1]["agent"] == "default"


def test_non_ascii_is_written_as_utf8_with_lf(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [response("₹ 日本語")])
    client.complete(MODEL, "sys", MSGS)
    raw = trace.path.read_bytes()
    assert b"\r" not in raw
    assert "₹ 日本語".encode() in raw


# ---- errors, budget, replay --------------------------------------------


def test_failed_call_is_traced_with_error_type(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [ValueError("boom")])
    with pytest.raises(ValueError):
        client.complete(MODEL, "sys", MSGS, agent="risk")
    call = lines(trace)[1]
    assert call["agent"] == "risk"
    assert call["error"] == "ValueError"
    assert call["response"] is None
    assert call["cost_usd"] == 0.0


def test_budget_breach_is_traced_before_raising(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [response("big", 100_000, 100_000)], budget=1.0)
    with pytest.raises(BudgetExceeded):
        client.complete(MODEL, "sys", MSGS)
    call = lines(trace)[1]
    assert call["response"] == "big"
    assert call["cost_usd"] == pytest.approx(1.2)
    assert call["error"] == "BudgetExceeded"


def test_replayed_call_is_traced_as_cached_and_free(tmp_path: Path) -> None:
    rec, _ = make_client(tmp_path, [response("x", 1000, 500)], cache_mode=CacheMode.RECORD)
    rec.complete(MODEL, "sys", MSGS)
    rep, trace = make_client(tmp_path, [], cache_mode=CacheMode.REPLAY)
    rep.complete(MODEL, "sys", MSGS)
    call = lines(trace)[1]
    assert call["cached"] is True
    assert call["cost_usd"] == 0.0
    assert call["response"] == "x"


def test_trace_never_contains_the_api_key(tmp_path: Path) -> None:
    client, trace = make_client(tmp_path, [response(), ValueError("boom")])
    client.complete(MODEL, "sys", MSGS)
    with pytest.raises(ValueError):
        client.complete(MODEL, "sys", MSGS)
    assert FAKE_KEY.encode() not in trace.path.read_bytes()


# ---- summary ------------------------------------------------------------


def traced_run(tmp_path: Path) -> TraceWriter:
    client, trace = make_client(
        tmp_path,
        [response("a", 1000, 500), response("b", 2000, 1000), response("c", 100, 50)],
    )
    client.complete(MODEL, "sys", MSGS, agent="analyst")
    client.complete(MODEL, "sys", MSGS, agent="analyst")
    client.complete(MODEL, "sys", MSGS, agent="trader")
    return trace


def test_summarize_groups_cost_and_time_by_agent(tmp_path: Path) -> None:
    trace = traced_run(tmp_path)
    s = summarize(trace.path)
    by_agent = {a.agent: a for a in s.agents}
    assert set(by_agent) == {"analyst", "trader"}
    analyst = by_agent["analyst"]
    assert analyst.calls == 2
    assert (analyst.input_tokens, analyst.output_tokens) == (3000, 1500)
    assert analyst.cost_usd == pytest.approx(0.006 + 0.015)
    assert analyst.time_s == pytest.approx(0.5)
    assert by_agent["trader"].calls == 1
    assert s.total.calls == 3
    assert s.total.cost_usd == pytest.approx(0.021 + 0.0007)
    assert s.total.time_s == pytest.approx(0.75)


def test_summarize_unknown_file_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="nope"):
        summarize(tmp_path / "nope.jsonl")


def test_main_prints_cost_and_time_per_agent_by_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = traced_run(tmp_path)
    assert main([str(trace.path)]) == 0
    out = capsys.readouterr().out
    assert "analyst" in out
    assert "trader" in out
    assert "TOTAL" in out
    assert "0.0210" in out  # analyst cost
    assert "0.50" in out  # analyst time


def test_main_resolves_a_run_id_in_the_trace_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = traced_run(tmp_path)
    monkeypatch.setenv("TA_TRACE_DIR", str(tmp_path / "traces"))
    assert main([trace.run_id]) == 0
    assert "analyst" in capsys.readouterr().out


def test_main_missing_run_exits_nonzero_with_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TA_TRACE_DIR", str(tmp_path))
    assert main(["no-such-run"]) != 0
    assert "no-such-run" in capsys.readouterr().err
