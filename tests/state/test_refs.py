"""S4.1: evidence references are paths into the run state, resolved against real fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_in.fixtures import FrozenDataProvider, Manifest
from ta_in.schemas import PortfolioRating, ResearchPlan
from ta_in.state import (
    TRADER_EVIDENCE_ROOTS,
    AgentName,
    AgentState,
    AnalystReport,
    DebateTurn,
    EvidenceRefError,
    available_refs,
    resolve_ref,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _full_state() -> AgentState:
    cell = Manifest.model_validate_json(
        (FIXTURES / "manifest.json").read_text(encoding="utf-8")
    ).cells[0]
    provider = FrozenDataProvider(FIXTURES)
    state = AgentState(
        run_id="r",
        ticker=cell.ticker,
        as_of=cell.as_of,
        identity=provider.identity(cell.ticker, as_of=cell.as_of),
        price_snapshot=provider.price_snapshot(cell.ticker, as_of=cell.as_of),
    )
    state = state.update(
        AgentName.MARKET_ANALYST, market_report=AnalystReport(analyst="market", text="m")
    )
    state = state.update(
        AgentName.NEWS_ANALYST,
        news_report=AnalystReport(analyst="news", text="", status="gap", gap_reason="boom"),
    )
    bull, bear = AgentName.BULL_RESEARCHER, AgentName.BEAR_RESEARCHER
    state = state.add_turn(bull, DebateTurn(speaker=bull, round=1, text="bull text"))
    state = state.add_turn(bear, DebateTurn(speaker=bear, round=1, text="bear text"))
    plan = ResearchPlan(recommendation=PortfolioRating.HOLD, rationale="r", strategic_actions="a")
    return state.update(AgentName.RESEARCH_MANAGER, plan=plan)


STATE = _full_state()


# --- valid paths -----------------------------------------------------------------------


def test_valid_paths_resolve_to_the_real_values() -> None:
    snap = STATE.price_snapshot
    assert snap is not None
    assert resolve_ref(STATE, "price_snapshot.last_close") == snap.last_close
    assert resolve_ref(STATE, "plan.recommendation") is PortfolioRating.HOLD
    assert resolve_ref(STATE, "market_report") == STATE.market_report
    assert resolve_ref(STATE, "investment_debate[1].text") == "bear text"
    assert resolve_ref(STATE, "identity.name") == STATE.identity.name  # type: ignore[union-attr]


# --- invalid paths ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "why"),
    [
        ("", "not a valid reference"),
        ("price snapshot", "not a valid reference"),
        ("plan..rationale", "not a valid reference"),
        ("plan.rationale()", "not a valid reference"),
        ("investment_debate[x]", "not a valid reference"),
        ("nonsense", "not an allowed"),
        ("decision", "not an allowed"),
        ("proposal.action", "not an allowed"),
        ("risk_debate[0]", "not an allowed"),
        ("price_snapshot.no_such_field", "no field"),
        ("plan.rationale.extra", "cannot take"),
        ("investment_debate[7]", "out of range"),
        ("fundamentals_report", "not set"),  # a field that is still None
        ("news_report", "gap"),  # a report that failed has nothing to cite
        ("news_report.text", "gap"),
    ],
)
def test_bad_references_raise_with_a_reason(ref: str, why: str) -> None:
    with pytest.raises(EvidenceRefError, match=why):
        resolve_ref(STATE, ref)


def test_a_bad_reference_error_is_a_value_error_that_names_the_reference() -> None:
    with pytest.raises(ValueError, match="plan.nope"):
        resolve_ref(STATE, "plan.nope")


def test_the_allowed_roots_exclude_the_proposal_the_risk_debate_and_the_decision() -> None:
    assert not {"proposal", "risk_debate", "decision"} & set(TRADER_EVIDENCE_ROOTS)
    assert "plan" in TRADER_EVIDENCE_ROOTS


# --- available_refs --------------------------------------------------------------------


def test_available_refs_lists_only_what_can_be_cited_in_a_fixed_order() -> None:
    refs = available_refs(STATE)
    assert refs == available_refs(STATE)  # stable
    assert "price_snapshot.last_close" in refs
    assert "plan.recommendation" in refs
    assert "market_report" in refs
    assert "investment_debate[0]" in refs and "investment_debate[1]" in refs
    assert "news_report" not in refs  # a gap
    assert "fundamentals_report" not in refs  # not set
    assert "decision" not in refs
    assert all(resolve_ref(STATE, r) is not None for r in refs)  # every one resolves
    roots = [r.split(".")[0].split("[")[0] for r in refs]
    assert roots == sorted(roots, key=TRADER_EVIDENCE_ROOTS.index)  # roots in declared order
