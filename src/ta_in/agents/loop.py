"""The generic agent loop: call the model, run its tool calls, repeat until it finishes.

Every agent (analysts, researchers, trader, risk team, managers) runs on this. The loop knows
nothing about `AgentState`: callers pass a prompt and tools, get an `AgentResult`, and write their
own field themselves.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from ta_in.agents.tools import Tool, build_registry
from ta_in.data.point_in_time import PointInTimeError
from ta_in.llm.client import BudgetExceeded
from ta_in.llm.turn import LLMTurn, ToolUse
from ta_in.state.models import AgentName

log = logging.getLogger(__name__)

# Errors that must never be turned into a tool result: a look-ahead leak is a bug to fix
# (hard rule 1), and a spent budget has to stop the run.
_FATAL = (PointInTimeError, BudgetExceeded)


class TurnClient(Protocol):
    """What the loop needs from a model client. `LLMClient` satisfies it; tests pass a script."""

    def complete_turn(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = ...,
        tools: list[dict[str, Any]] | None = ...,
        agent: str = ...,
    ) -> LLMTurn: ...


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    input: dict[str, Any]
    is_error: bool


class AgentResult(BaseModel):
    """How a run ended. `stopped_by` is the model's stop reason ("end_turn", "max_tokens", ...)
    or "step_limit" when the safety net ended the run."""

    model_config = ConfigDict(frozen=True)

    text: str
    messages: list[dict[str, Any]]
    steps: int
    stopped_by: str
    tool_calls: list[ToolCallRecord]


def run_agent(
    client: TurnClient,
    *,
    agent: AgentName,
    model: str,
    system: str,
    prompt: str,
    tools: Sequence[Tool] = (),
    max_steps: int = 10,
    max_tokens: int = 16000,
) -> AgentResult:
    """Run one agent to completion. `max_steps` counts model calls and ends the run cleanly."""
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    registry = build_registry(tools)
    schemas = [t.schema() for t in tools] or None

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    calls: list[ToolCallRecord] = []
    last_text = ""

    for step in range(1, max_steps + 1):
        turn = client.complete_turn(
            model=model,
            system=system,
            messages=list(messages),
            max_tokens=max_tokens,
            tools=schemas,
            agent=agent.value,
        )
        messages.append({"role": "assistant", "content": turn.content})
        uses = turn.tool_uses
        if turn.stop_reason != "tool_use" or not uses:
            return _result(turn.text, messages, step, turn.stop_reason, calls)
        last_text = turn.text or last_text  # for a step-limited run: the last thing it said

        results: list[dict[str, Any]] = []
        for use in uses:
            block, is_error = _run_tool(registry, use)
            results.append(block)
            calls.append(ToolCallRecord(name=use.name, input=use.input, is_error=is_error))
        messages.append({"role": "user", "content": results})

    log.warning("agent %s hit the step limit (%d model calls)", agent.value, max_steps)
    return _result(last_text, messages, max_steps, "step_limit", calls)


def _result(
    text: str,
    messages: list[dict[str, Any]],
    steps: int,
    stopped_by: str,
    calls: list[ToolCallRecord],
) -> AgentResult:
    return AgentResult(
        text=text, messages=messages, steps=steps, stopped_by=stopped_by, tool_calls=calls
    )


def _run_tool(registry: dict[str, Tool], use: ToolUse) -> tuple[dict[str, Any], bool]:
    """Run one tool call. Any failure comes back as a structured error the model can read."""
    tool = registry.get(use.name)
    if tool is None:
        known = ", ".join(sorted(registry)) or "none"
        return _error_block(use, "UnknownTool", f"no tool named {use.name!r}; available: {known}")
    try:
        args = tool.input_model.model_validate(use.input)
    except ValidationError as exc:
        return _error_block(use, "InvalidToolInput", _short(str(exc)))
    try:
        value = tool.fn(args)
    except _FATAL:
        raise
    except Exception as exc:
        log.info("tool %s failed: %s: %s", use.name, type(exc).__name__, exc)
        return _error_block(use, type(exc).__name__, _short(str(exc)))
    return {"type": "tool_result", "tool_use_id": use.id, "content": _to_json(value)}, False


def _error_block(use: ToolUse, error_type: str, message: str) -> tuple[dict[str, Any], bool]:
    body = json.dumps({"error": {"type": error_type, "message": message}}, ensure_ascii=False)
    return (
        {"type": "tool_result", "tool_use_id": use.id, "content": body, "is_error": True},
        True,
    )


def _to_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _short(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit] + "..."
