"""Per-run JSONL trace of LLM calls, plus the `trace-summary` command."""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TraceWriter:
    """One instance = one run = exactly one `<run_id>.jsonl` file."""

    def __init__(self, trace_dir: Path, run_id: str | None = None) -> None:
        self.run_id = run_id or new_run_id()
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = trace_dir / f"{self.run_id}.jsonl"
        self._lock = threading.Lock()  # analysts run in parallel threads and share one trace
        with self.path.open("xb"):  # "x": a second run can never reuse or overwrite a trace
            pass
        self._write({"type": "run_start", "run_id": self.run_id, "ts": _now()})

    def _write(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock, self.path.open("ab") as f:  # bytes: no CRLF translation on Windows
            f.write(line.encode("utf-8"))

    def log_call(
        self,
        *,
        agent: str,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        response: str | None,
        tool_calls: list[dict[str, Any]],
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_s: float,
        cached: bool,
        attempts: int,
        error: str | None,
    ) -> None:
        self._write(
            {
                "type": "call",
                "ts": _now(),
                "agent": agent,
                "model": model,
                "prompt": {"system": system, "messages": messages},
                "response": response,
                "tool_calls": tool_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "latency_s": latency_s,
                "cached": cached,
                "attempts": attempts,
                "error": error,
            }
        )


@dataclass
class AgentSummary:
    agent: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    time_s: float = 0.0  # sum of call latencies, not wall-clock


@dataclass
class RunSummary:
    run_id: str
    agents: list[AgentSummary]
    total: AgentSummary


def summarize(path: Path) -> RunSummary:
    if not path.exists():
        raise FileNotFoundError(f"trace file not found: {path}")
    by_agent: dict[str, AgentSummary] = {}
    total = AgentSummary("TOTAL")
    run_id = path.stem
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        if event["type"] == "run_start":
            run_id = event["run_id"]
            continue
        if event["type"] != "call":
            continue
        for row in (by_agent.setdefault(event["agent"], AgentSummary(event["agent"])), total):
            row.calls += 1
            row.input_tokens += event["input_tokens"]
            row.output_tokens += event["output_tokens"]
            row.cost_usd += event["cost_usd"]
            row.time_s += event["latency_s"]
    return RunSummary(run_id, sorted(by_agent.values(), key=lambda a: a.agent), total)


def _resolve(run: str) -> Path:
    candidate = Path(run)
    if candidate.exists():
        return candidate
    trace_dir = Path(os.environ.get("TA_TRACE_DIR", "traces"))
    return trace_dir / f"{run}.jsonl"


def _format(summary: RunSummary) -> str:
    header = f"{'agent':<20}{'calls':>6}{'in_tok':>10}{'out_tok':>10}{'cost_usd':>12}{'time_s':>9}"
    rows = [
        f"{a.agent:<20}{a.calls:>6}{a.input_tokens:>10}{a.output_tokens:>10}"
        f"{a.cost_usd:>12.4f}{a.time_s:>9.2f}"
        for a in [*summary.agents, summary.total]
    ]
    return "\n".join([f"run {summary.run_id}", header, *rows])


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for `trace-summary <run-id-or-path>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: trace-summary <run-id-or-path>", file=sys.stderr)
        return 2
    try:
        summary = summarize(_resolve(args[0]))
    except FileNotFoundError:
        print(f"no trace found for run {args[0]!r}", file=sys.stderr)
        return 1
    print(_format(summary))
    return 0
