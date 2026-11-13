"""S3.3: the research stage for 0 to 3 debate rounds, and exact replay (offline, real LLMClient)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_market_analyst import FIXTURES, _cells, _state

from ta_in.agents import run_research_team
from ta_in.config import Settings
from ta_in.fixtures import FrozenDataProvider
from ta_in.llm import CacheMiss, CacheMode, LLMClient, ResponseCache, StructuredOutputError
from ta_in.schemas import ResearchPlan
from ta_in.state import AgentName, AgentState, AnalystReport

QUICK = "claude-haiku-5-5"
DEEP = "claude-sonnet-5-5"
_ROUND = re.compile(r"round (\d+) argument")


def _bull(n: int) -> str:
    return f"Bull case for round {n}: the thesis holds up well on the evidence."


def _bear(n: int) -> str:
    return f"Bear case for round {n}: valuation is already stretched against peers."


def _usage() -> Any:
    return SimpleNamespace(input_tokens=1000, output_tokens=500)


class StorySDK:
    """Stands in for the Anthropic SDK: researchers argue, the manager returns a plan."""

    def __init__(self, quote: bool = True) -> None:
        self.quote = quote
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("tool_choice"):
            return self._plan(kwargs["messages"][0]["content"])
        system: str = kwargs["system"]
        prompt: str = kwargs["messages"][-1]["content"]
        n = int(_ROUND.findall(prompt)[-1])
        text = _bull(n) if system.startswith("You are the bull") else _bear(n)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=_usage(),
            stop_reason="end_turn",
        )

    def _plan(self, prompt: str) -> Any:
        if self.quote and "Bull case for round 1" in prompt:
            rationale = f'Bull: "{_bull(1)[:-1]}". Bear: "{_bear(1)[:-1]}". Hold.'
        else:
            rationale = "Judged on the reports alone. Hold."
        payload = {"recommendation": "Hold", "rationale": rationale, "strategic_actions": "Wait."}
        block = SimpleNamespace(type="tool_use", id="tu_1", name="submit_result", input=payload)
        return SimpleNamespace(content=[block], usage=_usage(), stop_reason="tool_use")


class ForbiddenSDK:
    """Replay must never reach the network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise AssertionError("replay called the SDK")


def _settings() -> Settings:
    return Settings(_env_file=None, ANTHROPIC_API_KEY="sk-test-key", budget_usd=1.0)  # type: ignore[call-arg]


def _client(tmp_path: Path, mode: CacheMode, sdk: Any) -> LLMClient:
    return LLMClient(
        _settings(),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
    )


def _start() -> AgentState:
    provider = FrozenDataProvider(FIXTURES)
    cell = _cells()[0]
    state = _state(provider, cell.ticker, cell.as_of)
    state = state.update(
        AgentName.MARKET_ANALYST, market_report=AnalystReport(analyst="market", text="market ok")
    )
    state = state.update(
        AgentName.NEWS_ANALYST, news_report=AnalystReport(analyst="news", text="news ok")
    )
    return state.update(
        AgentName.FUNDAMENTALS_ANALYST,
        fundamentals_report=AnalystReport(analyst="fundamentals", text="fundamentals ok"),
    )


def _run(client: LLMClient, rounds: int, state: AgentState | None = None) -> AgentState:
    return run_research_team(
        client, state or _start(), debate_model=QUICK, manager_model=DEEP, rounds=rounds
    )


# --- criterion 1: 0, 1, 2 (and 3) rounds run end to end --------------------------------


@pytest.mark.parametrize("rounds", [0, 1, 2, 3])
def test_each_round_count_runs_end_to_end(tmp_path: Path, rounds: int) -> None:
    sdk = StorySDK()
    new = _run(_client(tmp_path, CacheMode.OFF, sdk), rounds)

    assert len(new.investment_debate) == 2 * rounds
    assert [t.round for t in new.investment_debate] == [
        r for r in range(1, rounds + 1) for _ in (0, 1)
    ]
    assert [t.speaker for t in new.investment_debate] == [
        AgentName.BULL_RESEARCHER,
        AgentName.BEAR_RESEARCHER,
    ] * rounds
    assert isinstance(new.plan, ResearchPlan)
    assert len(sdk.calls) == 2 * rounds + 1  # the debate turns, then one manager call


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_debate_uses_the_quick_model_and_the_manager_the_deep_one(
    tmp_path: Path, rounds: int
) -> None:
    sdk = StorySDK()
    _run(_client(tmp_path, CacheMode.OFF, sdk), rounds)
    assert [c["model"] for c in sdk.calls] == [QUICK] * (2 * rounds) + [DEEP]


def test_zero_rounds_the_manager_judges_the_reports_directly(tmp_path: Path) -> None:
    sdk = StorySDK()
    new = _run(_client(tmp_path, CacheMode.OFF, sdk), 0)
    assert len(sdk.calls) == 1
    assert "No debate took place" in sdk.calls[0]["messages"][0]["content"]
    assert new.plan is not None and new.investment_debate == []


def test_with_a_debate_the_quote_rule_still_applies(tmp_path: Path) -> None:
    sdk = StorySDK(quote=False)  # the manager never quotes
    with pytest.raises(StructuredOutputError):
        _run(_client(tmp_path, CacheMode.OFF, sdk), 1)
    assert len(sdk.calls) == 2 + 2  # two debate turns, then the manager and its one retry


# --- criterion 2: replayed runs match recorded runs exactly ----------------------------


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_a_replayed_run_is_identical_to_the_recorded_run(tmp_path: Path, rounds: int) -> None:
    recorded_sdk = StorySDK()
    recorder = _client(tmp_path, CacheMode.RECORD, recorded_sdk)
    recorded = _run(recorder, rounds)

    forbidden = ForbiddenSDK()
    replayer = _client(tmp_path, CacheMode.REPLAY, forbidden)
    replayed = _run(replayer, rounds)

    assert forbidden.calls == []
    assert replayed.to_json() == recorded.to_json()
    assert len(replayer.records) == len(recorder.records) == 2 * rounds + 1
    assert all(r.cached and r.cost_usd == 0.0 for r in replayer.records)


def test_a_replay_of_a_different_run_fails_instead_of_inventing_output(tmp_path: Path) -> None:
    _run(_client(tmp_path, CacheMode.RECORD, StorySDK()), 1)
    replayer = _client(tmp_path, CacheMode.REPLAY, ForbiddenSDK())
    with pytest.raises(CacheMiss):
        _run(replayer, 2)  # the second round was never recorded
