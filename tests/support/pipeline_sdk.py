"""A scripted stand-in for the Anthropic SDK that answers every agent in the pipeline (offline).

Shared by the propagate and CLI tests. One `PipelineSDK` answers the analysts, researchers,
managers, trader and risk team with valid, quotable output; `ForbiddenSDK` fails if it is ever
called, which is how replay tests prove no model (and so no network) is used.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from strict_api import reject_forced_tool_choice

QUICK = "claude-haiku-5-5"
DEEP = "claude-sonnet-5-5"
_ROUND = re.compile(r"round (\d+) argument")
_NUMBERED = re.compile(r"^\[(\d+)\] ", re.MULTILINE)
_LAST_CLOSE = re.compile(r"Last close: ([\d.]+)")

# Who is speaking, from the start of the system prompt -> a short role name.
ROLES = (
    ("You are the market analyst", "market"),
    ("You are the news analyst", "news"),
    ("You are the fundamentals analyst", "fundamentals"),
    ("You are the bull researcher", "bull"),
    ("You are the bear researcher", "bear"),
    ("You are the research manager", "manager"),
    ("You are the trader", "trader"),
    ("You are the aggressive risk analyst", "aggressive"),
    ("You are the neutral risk analyst", "neutral"),
    ("You are the conservative risk analyst", "conservative"),
    ("You are the portfolio manager", "portfolio"),
)
TIER = {
    "market": QUICK,
    "news": QUICK,
    "fundamentals": QUICK,
    "bull": QUICK,
    "bear": QUICK,
    "manager": DEEP,
    "trader": QUICK,
    "aggressive": QUICK,
    "neutral": QUICK,
    "conservative": QUICK,
    "portfolio": DEEP,
}


def _says(role: str, n: int) -> str:
    return f"{role.capitalize()} view in round {n}: this is a clearly worded argument."


def _usage() -> Any:
    return SimpleNamespace(input_tokens=1000, output_tokens=500)


def _text(text: str) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=_usage(), stop_reason="end_turn"
    )


def _tool(name: str, payload: dict[str, Any], id_: str) -> Any:
    block = SimpleNamespace(type="tool_use", id=id_, name=name, input=payload)
    return SimpleNamespace(content=[block], usage=_usage(), stop_reason="tool_use")


def _quote(role: str) -> str:
    return _says(role, 1)[:-1]  # drop the final full stop, as a model quoting a sentence might


class PipelineSDK:
    """Answers every agent in the pipeline with valid, quotable output."""

    def __init__(self, failing: tuple[str, ...] = (), thesis_extra: str = "") -> None:
        self.failing = failing
        self.thesis_extra = thesis_extra  # appended to the decision thesis (a seeded figure)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    @staticmethod
    def role_of(system: str) -> str:
        for prefix, role in ROLES:
            if system.startswith(prefix):
                return role
        raise AssertionError(f"unknown agent: {system[:60]!r}")

    def roles(self) -> list[str]:
        return [self.role_of(c["system"]) for c in self.calls]

    def _create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        reject_forced_tool_choice(kw)
        role = self.role_of(kw["system"])
        if role in self.failing:
            raise RuntimeError(f"{role} is down")
        first = kw["messages"][0]["content"]
        assert isinstance(first, str)
        round_no = int(_ROUND.findall(first)[-1]) if _ROUND.findall(first) else 1

        if role == "market":
            if len(kw["messages"]) == 1:
                return _tool("get_indicator", {"kind": "sma", "window": 20, "last": 3}, "tu_i")
            close = _LAST_CLOSE.search(first)
            assert close is not None
            return _text(f"Closed at Rs {float(close.group(1)):.2f}; the trend looks neutral.")
        if role == "news":
            nums = [int(n) for n in _NUMBERED.findall(first)]
            return _text(
                f"Coverage is mixed [{nums[0]}] and routine [{nums[-1]}]. Net read: neutral."
            )
        if role in ("bull", "bear", "aggressive", "neutral", "conservative"):
            return _text(_says(role, round_no))
        if role == "manager":
            quotes = (
                f'Bull: "{_quote("bull")}". Bear: "{_quote("bear")}". '
                if "<debate>" in first
                else ""
            )
            payload = {
                "recommendation": "Overweight",
                "rationale": quotes + "Bull case carried it.",
                "strategic_actions": "Scale in over two weeks.",
            }
            return _tool("submit_result", payload, "tu_m")
        if role == "trader":
            payload = {
                "action": "Buy",
                "rating": "Overweight",
                "reasoning": "The plan favours a measured buy and the snapshot supports it.",
                "evidence": ["plan.recommendation", "price_snapshot.last_close"],
            }
            return _tool("submit_result", payload, "tu_t")
        if role == "portfolio":
            risk = "<risk_debate>" in first
            thesis = (
                " ".join(f'{r}: "{_quote(r)}".' for r in ("aggressive", "neutral", "conservative"))
                if risk
                else "Judged on the proposal alone."
            )
            payload = {
                "rating": "Overweight",
                "executive_summary": "Enter in two tranches and review in three months.",
                "investment_thesis": thesis + " Overweight, sized down." + self.thesis_extra,
            }
            return _tool("submit_result", payload, "tu_p")
        raise AssertionError(f"unscripted role {role}")


class ForbiddenSDK:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        raise AssertionError("replay called the SDK")
