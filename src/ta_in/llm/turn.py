"""One model turn: the assistant's text and tool_use blocks, plus why it stopped."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolUse(BaseModel):
    """A request from the model to run a tool."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    input: dict[str, Any]


class LLMTurn(BaseModel):
    """What the model said in one call.

    `content` holds the assistant blocks as plain dicts (`text` and `tool_use`): exactly what has
    to be sent back as the assistant message on the next call, and what the cache stores.
    """

    model_config = ConfigDict(frozen=True)

    content: list[dict[str, Any]]
    stop_reason: str = "end_turn"

    @property
    def text(self) -> str:
        return "".join(str(b["text"]) for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[ToolUse]:
        return [
            ToolUse(id=str(b["id"]), name=str(b["name"]), input=dict(b["input"]))
            for b in self.content
            if b.get("type") == "tool_use"
        ]

    @classmethod
    def from_text(cls, text: str, stop_reason: str = "end_turn") -> LLMTurn:
        return cls(content=[{"type": "text", "text": text}], stop_reason=stop_reason)

    @classmethod
    def from_sdk(cls, response: Any) -> LLMTurn:
        """Parse an Anthropic SDK response. Block types other than text and tool_use are dropped."""
        content: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input),
                    }
                )
        return cls(
            content=content, stop_reason=str(getattr(response, "stop_reason", None) or "end_turn")
        )
