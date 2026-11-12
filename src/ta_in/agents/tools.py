"""A tool an agent may call: a name, a description, a Pydantic input model and a function."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class Tool:
    """`fn` receives the validated `input_model` instance and returns a string, a dict or a
    Pydantic model; the loop turns the result into JSON for the model."""

    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any], Any]

    def schema(self) -> dict[str, Any]:
        """The tool definition in the shape the Anthropic Messages API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


def build_registry(tools: Sequence[Tool]) -> dict[str, Tool]:
    """Index tools by name. A repeated name is a bug, so it fails loudly."""
    registry: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in registry:
            raise ValueError(f"duplicate tool name {tool.name!r}")
        registry[tool.name] = tool
    return registry
