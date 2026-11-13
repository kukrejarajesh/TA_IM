"""Shared fakes for LLM tests (no network)."""

from types import SimpleNamespace
from typing import Any

from strict_api import reject_forced_tool_choice

from ta_in.config import Settings

FAKE_KEY = "sk-ant-FAKE-0123456789-secret"
MODEL = "claude-sonnet-5-5"  # $2 / $10 per million tokens


def make_settings(budget: float = 1.0) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        ANTHROPIC_API_KEY=FAKE_KEY,
        budget_usd=budget,
    )


def response(text: str = "ok", input_tokens: int = 1000, output_tokens: int = 500) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def tool_use_response(
    *uses: tuple[str, str, dict[str, Any]],
    text: str = "",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    stop_reason: str = "tool_use",
) -> Any:
    """An SDK response with optional text and tool_use blocks: uses are (id, name, input)."""
    blocks: list[Any] = [SimpleNamespace(type="text", text=text)] if text else []
    blocks += [SimpleNamespace(type="tool_use", id=i, name=n, input=inp) for i, n, inp in uses]
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=stop_reason,
    )


class FakeSDK:
    """Stands in for anthropic.Anthropic; replays a script of responses / exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        reject_forced_tool_choice(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
