"""Rules of the real Anthropic API that scripted fakes must enforce (so tests cannot hide them).

The first real run (2026-11-13) was rejected with: `tool_choice: type "tool" and "any" are not
supported for this model`. Every fake SDK calls `reject_forced_tool_choice` on each request.
"""

from __future__ import annotations

from typing import Any


def reject_forced_tool_choice(request: dict[str, Any]) -> None:
    choice = request.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") in ("tool", "any"):
        raise AssertionError(
            f"tool_choice {choice!r} sent: current models reject type 'tool' and 'any' "
            "(real API: 400 invalid_request_error); leave tool_choice unset or use 'auto'"
        )
