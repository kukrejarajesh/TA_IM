"""S2.3: the market analyst, driven by a scripted model over the frozen fixture cells (offline)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ta_in.agents import MAX_INDICATORS, run_market_analyst, unverified_price_figures
from ta_in.agents.market_tools import MarketToolkit
from ta_in.data import IST, PointInTimeError, PriceBar, PriceSnapshot
from ta_in.fixtures import FrozenDataProvider, Manifest
from ta_in.llm import LLMTurn
from ta_in.state import AgentName, AgentState, AnalystReport

MODEL = "claude-sonnet-5-5"
FIXTURES = Path(__file__).parent.parent / "fixtures"
MANIFEST = FIXTURES / "manifest.json"

Script = Sequence[LLMTurn | Callable[[list[dict[str, Any]]], LLMTurn] | Exception]


def _tool_turn(*uses: tuple[str, str, dict[str, Any]]) -> LLMTurn:
    content = [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in uses]
    return LLMTurn(content=content, stop_reason="tool_use")


def _text_turn(text: str, stop_reason: str = "end_turn") -> LLMTurn:
    return LLMTurn(content=[{"type": "text", "text": text}], stop_reason=stop_reason)


class ScriptedModel:
    """Stands in for LLMClient. An item may be a callable that sees the messages so far."""

    def __init__(self, script: Script) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete_turn(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 16000,
        tools: list[dict[str, Any]] | None = None,
        agent: str = "default",
    ) -> LLMTurn:
        self.calls.append(
            {
                "system": system,
                "messages": copy.deepcopy(messages),
                "tools": tools,
                "agent": agent,
            }
        )
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(messages) if callable(item) else item


def _tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parsed content of every tool_result the model has been shown so far."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "user" and isinstance(message["content"], list):
            out += [json.loads(b["content"]) for b in message["content"] if not b.get("is_error")]
    return out


# --- fixture cells ---------------------------------------------------------------------


def _cells() -> list[Any]:
    if not MANIFEST.exists():
        return []
    return list(Manifest.model_validate_json(MANIFEST.read_text(encoding="utf-8")).cells)


def _state(provider: FrozenDataProvider, ticker: str, as_of: datetime) -> AgentState:
    return AgentState(
        run_id="r1",
        ticker=ticker,
        as_of=as_of,
        identity=provider.identity(ticker, as_of=as_of),
        price_snapshot=provider.price_snapshot(ticker, as_of=as_of),
    )


@pytest.mark.parametrize("cell", _cells(), ids=lambda c: f"{c.ticker}-{c.as_of.date()}")
def test_a_grounded_report_is_produced_for_every_fixture_cell(cell: Any) -> None:
    provider = FrozenDataProvider(FIXTURES)
    state = _state(provider, cell.ticker, cell.as_of)
    snap = state.price_snapshot
    assert snap is not None

    def final(messages: list[dict[str, Any]]) -> LLMTurn:
        sma = _tool_results(messages)[-1]["values"][-1]["value"]
        return _text_turn(
            f"{cell.ticker} closed at Rs {snap.last_close:.2f}, within a 52-week range of "
            f"Rs {snap.low_52w:.2f} to Rs {snap.high_52w:.2f}. The 20-day SMA sits near "
            f"Rs {sma:.2f}. RSI and trend support a neutral stance."
        )

    model = ScriptedModel(
        [
            _tool_turn(
                ("t1", "get_price_history", {"days": 5}),
                ("t2", "get_indicator", {"kind": "rsi"}),
                ("t3", "get_indicator", {"kind": "sma", "window": 20}),
            ),
            final,
        ]
    )
    new = run_market_analyst(model, state, provider, model=MODEL)

    report = new.market_report
    assert report is not None
    assert report.status == "ok", report.gap_reason
    assert report.analyst == "market"
    assert f"Rs {snap.last_close:.2f}" in report.text
    # The prompt carried the verified snapshot and the company.
    first_prompt = model.calls[0]["messages"][0]["content"]
    assert "VERIFIED PRICE SNAPSHOT" in first_prompt
    assert state.identity is not None and state.identity.name in first_prompt
    # Only the report changed.
    assert new.model_copy(update={"market_report": None}) == state
    assert model.calls[0]["agent"] == AgentName.MARKET_ANALYST.value


def test_the_fixture_cells_were_found() -> None:
    if not MANIFEST.exists():
        pytest.skip("real snapshots not frozen yet (run `freeze-fixtures`)")
    assert len(_cells()) == 20  # 5 tickers x (3 past dates + current)


# --- the grounding check ---------------------------------------------------------------


def _snap() -> PriceSnapshot:
    return PriceSnapshot(
        as_of_date=date(2026, 11, 4),
        last_close=2529.0,
        high_52w=3324.9,
        high_52w_date=date(2025, 12, 22),
        low_52w=1982.6,
        low_52w_date=date(2026, 7, 1),
        history_complete_52w=True,
        return_1d=0.01,
        return_5d=0.02,
        return_20d=0.03,
        return_60d=0.04,
    )


def test_figures_that_match_the_snapshot_pass() -> None:
    text = "Close Rs 2,529.00, high Rs 3324.9, low ₹1982.60, rounded Rs 2,529 and INR 3325."
    assert unverified_price_figures(text, _snap(), []) == []


def test_a_wrong_price_is_reported() -> None:
    bad = unverified_price_figures("It closed at Rs 2,650.00 today.", _snap(), [])
    assert bad == ["Rs 2,650.00"]


def test_values_seen_from_tools_are_accepted() -> None:
    assert unverified_price_figures("SMA at Rs 2480.35", _snap(), [2480.351]) == []
    assert unverified_price_figures("SMA at Rs 2480.40", _snap(), [2480.351]) != []


def test_text_without_price_figures_has_nothing_to_check() -> None:
    assert unverified_price_figures("RSI is 62 and MACD is rising.", _snap(), []) == []


def test_a_report_with_a_wrong_price_becomes_a_gap() -> None:
    provider = FrozenDataProvider(FIXTURES)
    cells = _cells()
    if not cells:
        pytest.skip("real snapshots not frozen yet")
    cell = cells[0]
    state = _state(provider, cell.ticker, cell.as_of)
    assert state.price_snapshot is not None
    wrong = state.price_snapshot.last_close * 1.05
    model = ScriptedModel([_text_turn(f"Closed at Rs {wrong:.2f}, a strong breakout.")])
    report = run_market_analyst(model, state, provider, model=MODEL).market_report
    assert report is not None
    assert report.status == "gap"
    assert f"Rs {wrong:.2f}" in (report.gap_reason or "")


# --- tools: indicator budget, point in time, schema ------------------------------------


class LeakyProvider:
    """Returns a bar dated after as_of, as a buggy data source might."""

    def __init__(self, as_of: datetime) -> None:
        start = as_of.date() - timedelta(days=40)
        self.bars = [
            PriceBar(
                date=start + timedelta(days=i),
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                adj_close=100.5 + i,
                volume=1000,
            )
            for i in range(42)
        ]

    def prices(self, ticker: str, *, as_of: datetime) -> list[PriceBar]:
        return self.bars


def _toolkit(as_of: datetime, *, leak: bool = False) -> MarketToolkit:
    provider = LeakyProvider(as_of)
    if not leak:
        provider.bars = [b for b in provider.bars if b.date <= as_of.date()]
    return MarketToolkit(provider, "AAA.NS", as_of)  # type: ignore[arg-type]


AS_OF = datetime(2026, 11, 4, 16, 0, tzinfo=IST)


def _call(kit: MarketToolkit, name: str, args: dict[str, Any]) -> Any:
    tool = next(t for t in kit.tools() if t.name == name)
    return tool.fn(tool.input_model.model_validate(args))


def test_a_ninth_distinct_indicator_is_refused_but_repeats_are_free() -> None:
    kit = _toolkit(AS_OF)
    kinds = [("rsi", None), ("sma", 10), ("sma", 20), ("ema", 10), ("ema", 20)]
    kinds += [("atr", None), ("macd", None), ("vwma", None)]
    assert len(kinds) == MAX_INDICATORS
    for kind, window in kinds:
        _call(kit, "get_indicator", {"kind": kind, "window": window})
    _call(kit, "get_indicator", {"kind": "rsi"})  # same choice again: not a new indicator
    with pytest.raises(Exception, match="at most 8"):
        _call(kit, "get_indicator", {"kind": "boll_mid"})


def test_the_loop_returns_the_budget_error_to_the_model() -> None:
    provider, state = _any_state()
    uses = [(f"t{i}", "get_indicator", {"kind": "sma", "window": 5 + i}) for i in range(9)]
    model = ScriptedModel(
        [
            _tool_turn(*uses),
            lambda m: _text_turn("Used what I could; no price levels quoted."),
        ]
    )
    run_market_analyst(model, state, provider, model=MODEL)
    last_user = model.calls[1]["messages"][-1]["content"]
    assert [b.get("is_error", False) for b in last_user] == [False] * 8 + [True]
    assert "at most 8" in last_user[-1]["content"]


def test_tools_take_no_as_of_so_the_model_cannot_ask_for_the_future() -> None:
    kit = _toolkit(AS_OF)
    for tool in kit.tools():
        assert "as_of" not in tool.schema()["input_schema"]["properties"]


def test_price_history_never_goes_past_as_of() -> None:
    kit = _toolkit(AS_OF)
    out = _call(kit, "get_price_history", {"days": 60})
    assert out["bars"], "expected some bars"
    assert max(date.fromisoformat(b["date"]) for b in out["bars"]) <= AS_OF.date()


def test_a_leaky_provider_fails_loudly_not_quietly() -> None:
    kit = _toolkit(AS_OF, leak=True)
    with pytest.raises(PointInTimeError):
        _call(kit, "get_price_history", {"days": 10})
    with pytest.raises(PointInTimeError):
        _call(kit, "get_indicator", {"kind": "rsi"})


def test_the_toolkit_remembers_every_value_it_showed() -> None:
    kit = _toolkit(AS_OF)
    out = _call(kit, "get_price_history", {"days": 3})
    ind = _call(kit, "get_indicator", {"kind": "sma", "window": 5})
    assert out["bars"][-1]["close"] in kit.seen_values
    assert ind["values"][-1]["value"] in kit.seen_values


# --- analyst behaviour -----------------------------------------------------------------


def _any_state() -> tuple[FrozenDataProvider, AgentState]:
    cells = _cells()
    if not cells:
        pytest.skip("real snapshots not frozen yet")
    provider = FrozenDataProvider(FIXTURES)
    cell = cells[0]
    return provider, _state(provider, cell.ticker, cell.as_of)


def test_a_missing_snapshot_is_refused() -> None:
    provider, state = _any_state()
    bare = state.model_copy(update={"price_snapshot": None})
    with pytest.raises(ValueError, match="snapshot"):
        run_market_analyst(ScriptedModel([]), bare, provider, model=MODEL)


def test_hitting_the_step_limit_records_a_gap() -> None:
    provider, state = _any_state()
    looping = [_tool_turn(("t", "get_indicator", {"kind": "rsi"})) for _ in range(3)]
    new = run_market_analyst(ScriptedModel(looping), state, provider, model=MODEL, max_steps=3)
    report = new.market_report
    assert isinstance(report, AnalystReport)
    assert report.status == "gap"
    assert "step_limit" in (report.gap_reason or "")


def test_an_empty_answer_records_a_gap() -> None:
    provider, state = _any_state()
    new = run_market_analyst(ScriptedModel([_text_turn("  ")]), state, provider, model=MODEL)
    assert new.market_report is not None
    assert new.market_report.status == "gap"


def test_a_cut_off_answer_records_a_gap() -> None:
    provider, state = _any_state()
    model = ScriptedModel([_text_turn("Reliance closed", stop_reason="max_tokens")])
    new = run_market_analyst(model, state, provider, model=MODEL)
    assert new.market_report is not None
    assert new.market_report.status == "gap"
    assert "max_tokens" in (new.market_report.gap_reason or "")


def test_the_system_prompt_states_the_indicator_budget_and_the_grounding_rule() -> None:
    provider, state = _any_state()
    model = ScriptedModel([_text_turn("No price levels to quote here.")])
    run_market_analyst(model, state, provider, model=MODEL)
    system = model.calls[0]["system"]
    assert "8" in system
    assert "Rs" in system
