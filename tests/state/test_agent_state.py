"""S2.1: typed AgentState. Each agent writes only its own fields; state round-trips through JSON."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ta_in.data import IST
from ta_in.fixtures import FrozenDataProvider
from ta_in.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TraderAction,
    TraderProposal,
)
from ta_in.state import (
    OWNERSHIP,
    AgentName,
    AgentState,
    AnalystReport,
    DebateTurn,
    StateWriteError,
)

AS_OF = datetime(2026, 10, 28, 16, 0, tzinfo=IST)
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _state() -> AgentState:
    return AgentState(run_id="run-1", ticker="RELIANCE.NS", as_of=AS_OF)


def _report(analyst: str, **kw: object) -> AnalystReport:
    return AnalystReport(analyst=analyst, text=f"{analyst} report", **kw)  # type: ignore[arg-type]


def _plan() -> ResearchPlan:
    return ResearchPlan(
        recommendation=PortfolioRating.OVERWEIGHT,
        rationale="bull won",
        strategic_actions="scale in",
    )


def _proposal() -> TraderProposal:
    return TraderProposal(action=TraderAction.BUY, reasoning="plan says buy", entry_price=1450.5)


def _decision() -> PortfolioDecision:
    return PortfolioDecision(
        rating=PortfolioRating.OVERWEIGHT, executive_summary="buy on dips", investment_thesis="..."
    )


# One valid value per writable field, keyed by field name.
_SAMPLE_VALUES: dict[str, object] = {
    "run_id": "run-2",
    "ticker": "TCS.NS",
    "as_of": AS_OF,
    "market_report": _report("market"),
    "news_report": _report("news"),
    "fundamentals_report": _report("fundamentals"),
    "plan": _plan(),
    "proposal": _proposal(),
    "decision": _decision(),
}
_SINGLE_VALUE_FIELDS = set(_SAMPLE_VALUES) - {"run_id", "ticker", "as_of"}
_LIST_FIELDS = {"investment_debate", "risk_debate"}


# --- criterion 1: each agent can only write its own fields ------------------------------


def test_every_agent_has_an_ownership_entry_and_every_field_has_an_owner() -> None:
    assert set(OWNERSHIP) == set(AgentName)
    owned = [f for fields in OWNERSHIP.values() for f in fields]
    assert set(owned) == set(AgentState.model_fields)


def test_single_value_fields_have_exactly_one_owner() -> None:
    for field in _SAMPLE_VALUES:
        owners = [a for a, fields in OWNERSHIP.items() if field in fields]
        assert len(owners) == 1, (field, owners)


@pytest.mark.parametrize("agent", list(AgentName))
def test_an_agent_can_write_each_of_its_own_single_value_fields(agent: AgentName) -> None:
    for field in OWNERSHIP[agent] & _SINGLE_VALUE_FIELDS:
        out = _state().update(agent, **{field: _SAMPLE_VALUES[field]})
        assert getattr(out, field) == _SAMPLE_VALUES[field]


@pytest.mark.parametrize("agent", list(AgentName))
def test_an_agent_cannot_write_a_field_it_does_not_own(agent: AgentName) -> None:
    for field in _SINGLE_VALUE_FIELDS - OWNERSHIP[agent]:
        with pytest.raises(StateWriteError, match=field):
            _state().update(agent, **{field: _SAMPLE_VALUES[field]})


def test_the_market_analyst_cannot_touch_other_agents_work() -> None:
    state = _state()
    for field in ("news_report", "plan", "proposal", "decision"):
        with pytest.raises(StateWriteError):
            state.update(AgentName.MARKET_ANALYST, **{field: _SAMPLE_VALUES[field]})


def test_an_analyst_cannot_write_another_analysts_report() -> None:
    with pytest.raises(StateWriteError):
        _state().update(AgentName.NEWS_ANALYST, market_report=_report("market"))


def test_only_the_orchestrator_writes_the_run_inputs() -> None:
    state = _state()
    with pytest.raises(StateWriteError):
        state.update(AgentName.TRADER, ticker="TCS.NS")
    # run inputs are always set, so changing one is an explicit replace
    assert state.update(AgentName.ORCHESTRATOR, replace=True, ticker="TCS.NS").ticker == "TCS.NS"
    with pytest.raises(StateWriteError, match="already set"):
        state.update(AgentName.ORCHESTRATOR, ticker="TCS.NS")


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(StateWriteError, match="no_such_field"):
        _state().update(AgentName.ORCHESTRATOR, no_such_field=1)


def test_a_wrongly_typed_value_fails_loudly() -> None:
    with pytest.raises(ValueError):
        _state().update(AgentName.RESEARCH_MANAGER, plan="just a string")


# --- debates ----------------------------------------------------------------------------


def test_bull_and_bear_add_turns_in_order() -> None:
    s = _state()
    s = s.add_turn(
        AgentName.BULL_RESEARCHER,
        DebateTurn(speaker=AgentName.BULL_RESEARCHER, round=1, text="buy"),
    )
    s = s.add_turn(
        AgentName.BEAR_RESEARCHER,
        DebateTurn(speaker=AgentName.BEAR_RESEARCHER, round=1, text="sell"),
    )
    assert [t.speaker for t in s.investment_debate] == [
        AgentName.BULL_RESEARCHER,
        AgentName.BEAR_RESEARCHER,
    ]
    assert s.risk_debate == []


def test_the_three_risk_analysts_write_the_risk_debate_only() -> None:
    s = _state()
    for agent in (AgentName.AGGRESSIVE_RISK, AgentName.NEUTRAL_RISK, AgentName.CONSERVATIVE_RISK):
        s = s.add_turn(agent, DebateTurn(speaker=agent, round=1, text="..."))
    assert len(s.risk_debate) == 3
    assert s.investment_debate == []


def test_a_risk_analyst_cannot_add_to_the_investment_debate() -> None:
    turn = DebateTurn(speaker=AgentName.AGGRESSIVE_RISK, round=1, text="x")
    with pytest.raises(StateWriteError):
        _state().add_turn(AgentName.AGGRESSIVE_RISK, turn, debate="investment")


def test_a_turn_must_be_spoken_by_the_caller() -> None:
    turn = DebateTurn(speaker=AgentName.BEAR_RESEARCHER, round=1, text="x")
    with pytest.raises(StateWriteError, match="speaker"):
        _state().add_turn(AgentName.BULL_RESEARCHER, turn)


def test_an_agent_with_no_debate_cannot_add_turns() -> None:
    turn = DebateTurn(speaker=AgentName.TRADER, round=1, text="x")
    with pytest.raises(StateWriteError):
        _state().add_turn(AgentName.TRADER, turn)


def test_debate_rounds_start_at_one() -> None:
    with pytest.raises(ValueError):
        DebateTurn(speaker=AgentName.BULL_RESEARCHER, round=0, text="x")


# --- set-once, immutability -------------------------------------------------------------


def test_a_field_cannot_be_silently_overwritten() -> None:
    s = _state().update(AgentName.RESEARCH_MANAGER, plan=_plan())
    with pytest.raises(StateWriteError, match="already set"):
        s.update(AgentName.RESEARCH_MANAGER, plan=_plan())


def test_replace_allows_an_explicit_overwrite() -> None:
    s = _state().update(AgentName.RESEARCH_MANAGER, plan=_plan())
    newer = ResearchPlan(
        recommendation=PortfolioRating.HOLD, rationale="r", strategic_actions="wait"
    )
    assert s.update(AgentName.RESEARCH_MANAGER, replace=True, plan=newer).plan == newer


def test_update_returns_a_new_state_and_leaves_the_old_one_unchanged() -> None:
    before = _state()
    after = before.update(AgentName.TRADER, proposal=_proposal())
    assert before.proposal is None
    assert after.proposal == _proposal()
    assert after is not before


def test_direct_assignment_is_impossible() -> None:
    with pytest.raises(ValueError):
        _state().plan = _plan()  # type: ignore[misc]


# --- validation -------------------------------------------------------------------------


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError):
        AgentState(run_id="r", ticker="RELIANCE.NS", as_of=datetime(2026, 10, 28, 16, 0))


def test_a_gap_report_needs_a_reason() -> None:
    with pytest.raises(ValueError):
        AnalystReport(analyst="news", text="", status="gap")
    gap = AnalystReport(analyst="news", text="", status="gap", gap_reason="provider failed")
    assert gap.insufficient_data is False


def test_insufficient_data_flag_is_carried_on_a_report() -> None:
    assert _report("news", insufficient_data=True).insufficient_data is True


# --- criterion 2: serialises to JSON and back without loss ------------------------------


def _full_state() -> AgentState:
    frozen = FrozenDataProvider(FIXTURES)
    s = _state()
    s = s.update(
        AgentName.ORCHESTRATOR,
        identity=frozen.identity("RELIANCE.NS", as_of=AS_OF),
        price_snapshot=frozen.price_snapshot("RELIANCE.NS", as_of=AS_OF),
    )
    s = s.update(AgentName.MARKET_ANALYST, market_report=_report("market"))
    s = s.update(AgentName.NEWS_ANALYST, news_report=_report("news", insufficient_data=True))
    s = s.update(
        AgentName.FUNDAMENTALS_ANALYST,
        fundamentals_report=AnalystReport(
            analyst="fundamentals", text="", status="gap", gap_reason="withheld: past date"
        ),
    )
    for rnd in (1, 2):
        for agent in (AgentName.BULL_RESEARCHER, AgentName.BEAR_RESEARCHER):
            s = s.add_turn(agent, DebateTurn(speaker=agent, round=rnd, text=f"{agent} {rnd}"))
    s = s.update(AgentName.RESEARCH_MANAGER, plan=_plan())
    s = s.update(AgentName.TRADER, proposal=_proposal())
    for agent in (AgentName.AGGRESSIVE_RISK, AgentName.NEUTRAL_RISK, AgentName.CONSERVATIVE_RISK):
        s = s.add_turn(agent, DebateTurn(speaker=agent, round=1, text=f"{agent} view"))
    return s.update(AgentName.PORTFOLIO_MANAGER, decision=_decision())


def test_a_fully_populated_state_round_trips_without_loss() -> None:
    state = _full_state()
    assert state.identity is not None
    assert state.price_snapshot is not None
    again = AgentState.from_json(state.to_json())
    assert again == state
    assert again.to_json() == state.to_json()
    assert again.as_of.utcoffset() == state.as_of.utcoffset()
    assert again.decision is not None
    assert again.decision.price_target is None  # an absent optional stays absent


def test_an_empty_state_round_trips() -> None:
    state = _state()
    assert AgentState.from_json(state.to_json()) == state


def test_the_state_holds_the_frozen_price_snapshot_the_agents_will_quote() -> None:
    frozen = FrozenDataProvider(FIXTURES)
    snap = frozen.price_snapshot("RELIANCE.NS", as_of=AS_OF)
    state = _state().update(AgentName.ORCHESTRATOR, price_snapshot=snap)
    assert state.price_snapshot == snap
    assert state.price_snapshot is not None
    assert state.price_snapshot.as_of_date <= AS_OF.date()
