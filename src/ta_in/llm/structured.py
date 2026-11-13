"""Structured output: make the model fill a tool whose schema is a Pydantic model.

The tool is forced (`tool_choice`), so a well-behaved model answers with exactly one call to it.
The input is validated with the Pydantic model. A bad answer (text only, another tool, several
calls, a cut-off, or input that fails validation) is retried ONCE: the model is shown its own answer
and the error and asked again. A second failure raises `StructuredOutputError` and writes the raw
output of both attempts to the trace, so a bad run is never papered over.

Only malformed answers are retried. `BudgetExceeded`, `PointInTimeError` and every other exception
from the client propagate untouched. The retry goes through the normal client path, so both calls
are costed, traced and recorded for replay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ta_in.llm.trace import TraceWriter
from ta_in.llm.turn import LLMTurn

T = TypeVar("T", bound=BaseModel)

MAX_ERROR_CHARS = 2000  # the validation error shown to the model on the retry


class StructuredClient(Protocol):
    """What `complete_structured` needs from a client. `LLMClient` satisfies it."""

    def complete_turn(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = ...,
        tools: list[dict[str, Any]] | None = ...,
        agent: str = ...,
        tool_choice: dict[str, Any] | None = ...,
    ) -> LLMTurn: ...


@dataclass(frozen=True)
class Attempt:
    """One failed try: the raw output (tool input, or text if no tool was called) and why."""

    raw: Any
    stop_reason: str
    error: str


class StructuredOutputError(Exception):
    """The model did not produce valid structured output, even after one retry."""

    def __init__(self, agent: str, attempts: list[Attempt]) -> None:
        self.agent = agent
        self.attempts = attempts
        detail = "; ".join(f"attempt {i}: {a.error}" for i, a in enumerate(attempts, start=1))
        super().__init__(
            f"{agent}: no valid structured output after {len(attempts)} tries ({detail})"
        )


def _raw(turn: LLMTurn) -> Any:
    uses = turn.tool_uses
    if len(uses) == 1:
        return uses[0].input
    if uses:
        return [{"name": u.name, "input": u.input} for u in uses]
    return turn.text


def _check(
    turn: LLMTurn,
    output_model: type[T],
    tool_name: str,
    check: Callable[[T], str | None] | None,
) -> tuple[T | None, str]:
    """The parsed model, or None and a message saying what was wrong."""
    if turn.stop_reason == "max_tokens":
        return None, "the answer was cut off (max_tokens); keep it shorter"
    uses = turn.tool_uses
    if not uses:
        return None, f"you did not call the {tool_name} tool; answer only by calling it"
    if len(uses) != 1 or uses[0].name != tool_name:
        names = ", ".join(u.name for u in uses)
        return (
            None,
            f"call the {tool_name} tool exactly once and nothing else (you called: {names})",
        )
    try:
        parsed = output_model.model_validate(uses[0].input)
    except ValidationError as err:
        return None, f"the tool input did not match the schema: {err}"[:MAX_ERROR_CHARS]
    problem = check(parsed) if check is not None else None
    if problem:
        return None, problem[:MAX_ERROR_CHARS]
    return parsed, ""


def _retry_messages(
    messages: list[dict[str, Any]], turn: LLMTurn, error: str, tool_name: str
) -> list[dict[str, Any]]:
    """The conversation so far, the model's bad answer, and a reply that says what was wrong."""
    uses = turn.tool_uses
    if uses:  # every tool_use must be answered by a tool_result in the next user message
        reply: Any = [
            {"type": "tool_result", "tool_use_id": u.id, "content": error, "is_error": True}
            for u in uses
        ]
    else:
        reply = f"{error}. Call the {tool_name} tool now."
    content = turn.content or [{"type": "text", "text": "(empty answer)"}]
    return [
        *messages,
        {"role": "assistant", "content": content},
        {"role": "user", "content": reply},
    ]


def complete_structured(
    client: StructuredClient,
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    output_model: type[T],
    agent: str,
    trace: TraceWriter | None = None,
    tool_name: str = "submit_result",
    max_tokens: int = 16000,
    check: Callable[[T], str | None] | None = None,
) -> T:
    """Return a validated `output_model` from the model; at most two model calls are made.

    `check` is an optional meaning check run on a schema-valid result. It returns None when the
    result is fine, or a message when it is not; the message is treated like a validation error
    (shown to the model on the one retry, and in the failure trace if the retry fails too).
    """
    tool = {
        "name": tool_name,
        "description": f"Submit your final {output_model.__name__}. Call this exactly once.",
        "input_schema": output_model.model_json_schema(),
    }
    choice = {"type": "tool", "name": tool_name}
    attempts: list[Attempt] = []
    convo = list(messages)

    for attempt in (1, 2):
        turn = client.complete_turn(
            model=model,
            system=system,
            messages=convo,
            max_tokens=max_tokens,
            tools=[tool],
            agent=agent,
            tool_choice=choice,
        )
        parsed, error = _check(turn, output_model, tool_name, check)
        if parsed is not None:
            return parsed
        attempts.append(Attempt(raw=_raw(turn), stop_reason=turn.stop_reason, error=error))
        if attempt == 1:
            convo = _retry_messages(messages, turn, error, tool_name)

    if trace is not None:
        trace.log_event(
            "structured_failure",
            agent=agent,
            model=model,
            output_model=output_model.__name__,
            attempts=[
                {"raw": a.raw, "stop_reason": a.stop_reason, "error": a.error} for a in attempts
            ],
        )
    raise StructuredOutputError(agent, attempts)
