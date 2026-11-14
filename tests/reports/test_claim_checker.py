"""S5.3: the claim checker lists each number in the proposal and decision matching no source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ta_in.claims import ClaimReport, check_claims, extract_numbers
from ta_in.fixtures import FrozenDataProvider, Manifest
from ta_in.schemas import (
    PortfolioDecision,
    PortfolioRating,
    TraderAction,
    TraderProposal,
)
from ta_in.state import AgentName, AgentState, AnalystReport

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _base() -> AgentState:
    cell = Manifest.model_validate_json((FIXTURES / "manifest.json").read_text("utf-8")).cells[0]
    provider = FrozenDataProvider(FIXTURES)
    return AgentState(
        run_id="r",
        ticker=cell.ticker,
        as_of=cell.as_of,
        identity=provider.identity(cell.ticker, as_of=cell.as_of),
        price_snapshot=provider.price_snapshot(cell.ticker, as_of=cell.as_of),
    )


SNAP = _base().price_snapshot
assert SNAP is not None


def _state(
    *,
    thesis: str = "Hold.",
    summary: str = "Hold.",
    reasoning: str = "Hold.",
    sizing: str | None = None,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    horizon: str | None = None,
    market: str = "",
    news: str = "",
    fundamentals: str = "",
    gap_reason: str | None = None,
) -> AgentState:
    state = _base()
    state = state.update(
        AgentName.MARKET_ANALYST, market_report=AnalystReport(analyst="market", text=market)
    )
    news_report = (
        AnalystReport(analyst="news", text="", status="gap", gap_reason=gap_reason)
        if gap_reason
        else AnalystReport(analyst="news", text=news)
    )
    state = state.update(AgentName.NEWS_ANALYST, news_report=news_report)
    state = state.update(
        AgentName.FUNDAMENTALS_ANALYST,
        fundamentals_report=AnalystReport(analyst="fundamentals", text=fundamentals),
    )
    proposal = TraderProposal(
        action=TraderAction.HOLD,
        reasoning=reasoning,
        entry_price=entry,
        stop_loss=stop,
        position_sizing=sizing,
        rating=PortfolioRating.HOLD,
        evidence=["market_report"],
    )
    state = state.update(AgentName.TRADER, proposal=proposal)
    decision = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary=summary,
        investment_thesis=thesis,
        price_target=target,
        time_horizon=horizon,
    )
    return state.update(AgentName.PORTFOLIO_MANAGER, decision=decision)


def _unmatched(state: AgentState, **kw: Any) -> list[str]:
    return [u.text for u in check_claims(state, **kw).unmatched]


CLOSE = SNAP.last_close
HIGH, LOW = SNAP.high_52w, SNAP.low_52w
assert SNAP.return_5d is not None and SNAP.return_20d is not None
R5, R20 = SNAP.return_5d * 100, SNAP.return_20d * 100


# --- criterion 2: a seeded false figure is caught --------------------------------------


def test_a_seeded_false_figure_is_caught_and_correct_ones_are_not() -> None:
    good = _state(
        thesis=f"The close of Rs {CLOSE:,.2f} is well below the 52-week high of Rs {HIGH:,.2f}.",
        stop=LOW,
        target=HIGH,
    )
    report = check_claims(good)
    assert report.unmatched == [] and report.checked == report.matched > 0

    seeded = _state(
        thesis=f"The close of Rs {CLOSE:,.2f} is near Rs 9,999, a level it has never touched.",
        stop=4321.0,
        target=HIGH,
    )
    report = check_claims(seeded)
    wheres = {(u.where, u.text) for u in report.unmatched}
    assert ("decision.investment_thesis", "Rs 9,999") in wheres
    assert any(w == "proposal.stop_loss" for w, _ in wheres)
    assert len(report.unmatched) == 2
    assert report.checked == report.matched + len(report.unmatched)


def test_a_false_percent_and_a_false_crore_amount_are_caught() -> None:
    state = _state(
        thesis="The stock is up 42% and the company holds Rs 55,555 crore of cash.",
        fundamentals="Cash and equivalents were Rs 12,345 crore.",
    )
    assert set(_unmatched(state)) == {"42%", "Rs 55,555 crore"}


# --- matching --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [f"Rs {CLOSE:,.2f}", f"{CLOSE:.1f}", f"₹{CLOSE:.2f}", f"INR {CLOSE:.0f}", f"Rs. {CLOSE}"],
)
def test_the_snapshot_close_matches_however_it_is_written(written: str) -> None:
    assert _unmatched(_state(thesis=f"It closed at {written} today.")) == []


def test_the_52_week_high_and_low_and_the_returns_match() -> None:
    thesis = (
        f"High Rs {HIGH:,.2f}, low Rs {LOW:,.2f}, 5-day return {R5:+.2f}%, "
        f"20-day return {R20:.2f}%."
    )
    assert _unmatched(_state(thesis=thesis)) == []


def test_rounded_and_truncated_copies_match_but_a_wrong_digit_does_not() -> None:
    assert _unmatched(_state(thesis=f"Rs {CLOSE:.0f}")) == []
    assert _unmatched(_state(thesis=f"Rs {int(CLOSE)}")) == []
    assert _unmatched(_state(thesis=f"Rs {CLOSE + 30:.2f}")) == [f"Rs {CLOSE + 30:.2f}"]


def test_the_tolerance_is_applied_and_can_be_changed() -> None:
    inside = f"Rs {CLOSE * 1.004:.2f}"  # 0.4% away
    outside = f"Rs {CLOSE * 1.02:.2f}"  # 2% away
    assert _unmatched(_state(thesis=inside)) == []
    assert _unmatched(_state(thesis=outside)) == [outside]
    assert _unmatched(_state(thesis=outside), rel_tol=0.03) == []
    assert _unmatched(_state(thesis=inside), rel_tol=0.0) == [inside]


def test_a_percent_never_matches_a_currency_source_and_the_reverse() -> None:
    assert _unmatched(_state(thesis=f"Up {CLOSE:.0f}%")) == [f"{CLOSE:.0f}%"]
    assert _unmatched(_state(thesis=f"Rs {R5:.2f}")) == [f"Rs {R5:.2f}"]


def test_numbers_in_each_of_the_three_reports_are_valid_sources() -> None:
    state = _state(
        thesis="Support at Rs 1,111.50, RSI 61.7 and net profit of Rs 7,654 crore.",
        market="Support sits at Rs 1,111.50 and RSI is 61.7.",
        fundamentals="Net profit was Rs 7,654 crore.",
    )
    assert _unmatched(state) == []
    news_state = _state(thesis="The order is worth Rs 8,800 crore.", news="A Rs 8,800 crore order.")
    assert _unmatched(news_state) == []


def test_crore_lakh_and_billion_forms_compare_after_scaling() -> None:
    fundamentals = "Revenue was Rs 92,852 crore and Rs 3 lakh crore of assets."
    assert _unmatched(_state(thesis="Rs 928.52 billion revenue", fundamentals=fundamentals)) == []
    assert _unmatched(_state(thesis="Rs 3,00,000 crore of assets", fundamentals=fundamentals)) == []
    assert _unmatched(_state(thesis="Rs 92,852 lakh revenue", fundamentals=fundamentals)) != []


def test_a_gap_report_contributes_no_sources() -> None:
    state = _state(thesis="A Rs 7,777 level.", gap_reason="unverified figures: Rs 7,777")
    assert _unmatched(state) == ["Rs 7,777"]


def test_a_sign_difference_does_not_cause_a_mismatch() -> None:
    state = _state(thesis="A fall of 3.50%", market="Down -3.50% over the week.")
    assert _unmatched(state) == []


# --- extraction: what is not a claim ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "on 23 Oct the stock rose",
        "since Oct 23, 2026 it fell",
        "as of 2026-10-21 it closed",
        "dated 21/10/2026",
        "in 2026 the margin",
        "at 16:00 IST",
        "risk to reward of 1:2.3",
        "1. first point\n2) second point",
        "the 3rd session",
        "over 5 days and a 20-day average, a 200-day SMA, 12 weeks, 6 months",
        "three tranches written as 3 and 14 sessions",
        "Q3 and FY26 results",
        "build a 0.5x starter, then 1.5x, and a 2.5×ATR stop",  # real runs: sizing multipliers
    ],
)
def test_dates_years_times_ratios_markers_durations_and_small_counts_are_not_claims(
    text: str,
) -> None:
    assert extract_numbers(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a stop of Rs 3", ["Rs 3"]),
        ("3% of the portfolio", ["3%"]),
        ("buy 21 shares", ["21"]),
        ("a price of 1209.5", ["1209.5"]),
        ("Rs 1,209.50 and Rs 1,158.90", ["Rs 1,209.50", "Rs 1,158.90"]),
        ("range Rs 1,065–1,119", ["Rs 1,065", "1,119"]),
        ("up +9.97% and down -3.1%", ["+9.97%", "-3.1%"]),
        ("Rs 92,852 crore and Rs 3.5 lakh", ["Rs 92,852 crore", "Rs 3.5 lakh"]),
        ("2026 revenue Rs 2026", ["Rs 2026"]),
    ],
)
def test_real_claims_are_found(text: str, expected: list[str]) -> None:
    assert [n.text for n in extract_numbers(text)] == expected


def test_extracted_numbers_carry_value_places_and_kind() -> None:
    (a, b, c) = extract_numbers("Rs 1,209.50, 9.97% and Rs 12 crore")
    assert (a.raw, a.value, a.places, a.kind) == (1209.5, 1209.5, 2, "amount")
    assert (b.raw, b.places, b.kind) == (9.97, 2, "percent")
    assert (c.raw, c.value, c.places, c.kind) == (12.0, 12e7, 0, "amount")


def test_sources_keep_every_number_when_noise_rules_are_off() -> None:
    texts = "on 23 Oct, 3 tranches, 2026"
    assert extract_numbers(texts) == []
    assert [n.raw for n in extract_numbers(texts, skip_noise=False)] == [23.0, 3.0, 2026.0]


# --- which fields are claims -----------------------------------------------------------


def test_the_numeric_fields_are_claims_and_the_horizon_is_not() -> None:
    state = _state(
        entry=1450.5, stop=1380.25, target=1700.0, horizon="1-3 months, review in 20 days"
    )
    report = check_claims(state)
    assert {u.where for u in report.unmatched} == {
        "proposal.entry_price",
        "proposal.stop_loss",
        "decision.price_target",
    }


def test_numbers_in_every_text_field_are_found() -> None:
    state = _state(
        thesis="thesis Rs 8,001",
        summary="summary Rs 8,002",
        reasoning="reasoning Rs 8,003",
        sizing="sizing Rs 8,004",
    )
    assert {u.where for u in check_claims(state).unmatched} == {
        "decision.investment_thesis",
        "decision.executive_summary",
        "proposal.reasoning",
        "proposal.position_sizing",
    }


def test_a_missing_proposal_or_decision_is_an_error() -> None:
    state = _state()
    with pytest.raises(ValueError, match="proposal"):
        check_claims(state.model_copy(update={"proposal": None}))
    with pytest.raises(ValueError, match="decision"):
        check_claims(state.model_copy(update={"decision": None}))


# --- the report ------------------------------------------------------------------------


def test_counts_add_up_and_the_order_is_stable() -> None:
    state = _state(
        thesis=f"Rs {CLOSE:,.2f}, Rs 9,001 and Rs 9,002.",
        summary="Rs 9,003.",
        stop=9004.0,
    )
    report = check_claims(state)
    assert report.checked == report.matched + len(report.unmatched)
    assert [u.text for u in report.unmatched] == ["Rs 9,003", "Rs 9,001", "Rs 9,002", "9004.0"]
    assert check_claims(state) == report  # deterministic


def test_the_context_shows_the_words_around_the_number() -> None:
    state = _state(thesis="We would target a breakout above Rs 9,999 on strong volume next week.")
    (claim,) = check_claims(state).unmatched
    assert "breakout above Rs 9,999 on strong" in claim.context
    assert len(claim.context) <= 90


def test_render_for_none_one_and_many_unmatched() -> None:
    clean = check_claims(_state(thesis=f"Rs {CLOSE:,.2f}"))
    assert "all 1 numbers match" in clean.render()
    nothing = check_claims(_state())
    assert "no numbers to check" in nothing.render()
    one = check_claims(_state(thesis="Level Rs 9,999 matters."))
    text = one.render()
    assert "1 of 1 numbers match no source" in text
    assert "decision.investment_thesis" in text and "Rs 9,999" in text
    assert "derived" in text  # the note that derived figures can appear here
    many = check_claims(_state(thesis="Rs 9,001 Rs 9,002 Rs 9,003")).render()
    assert many.count("\n  - ") == 3


def test_the_report_round_trips_through_json() -> None:
    report = check_claims(_state(thesis="Rs 9,999 and Rs 8,888"))
    assert ClaimReport.model_validate_json(report.model_dump_json()) == report
