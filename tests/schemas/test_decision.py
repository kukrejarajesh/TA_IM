from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from ta_in.schemas import (
    RATING_REVIEW,
    RATINGS_5_TIER,
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TraderAction,
    TraderProposal,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "ta_in"
DECISION_SRC = SRC / "schemas" / "decision.py"

# ---- labels (acceptance 1) ----------------------------------------------


def test_rating_labels_match_v070_in_order() -> None:
    assert tuple(r.value for r in PortfolioRating) == (
        "Buy",
        "Overweight",
        "Hold",
        "Underweight",
        "Sell",
    )
    assert RATINGS_5_TIER == ("Buy", "Overweight", "Hold", "Underweight", "Sell")
    assert tuple(r.value for r in PortfolioRating) == RATINGS_5_TIER


def test_trader_action_labels_match_v070() -> None:
    assert tuple(a.value for a in TraderAction) == ("Buy", "Hold", "Sell")


def test_ratings_are_strings() -> None:
    assert PortfolioRating.BUY == "Buy"
    assert f"{PortfolioRating.UNDERWEIGHT}" == "Underweight"


@pytest.mark.parametrize("bad", ["buy", "BUY", "Strong Buy", "Neutral", ""])
def test_unknown_or_wrong_case_rating_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        PortfolioRating(bad)


def test_review_sentinel_is_not_a_tradeable_rating() -> None:
    assert RATING_REVIEW == "REVIEW"
    with pytest.raises(ValueError):
        PortfolioRating(RATING_REVIEW)


def test_source_file_is_noted_in_the_module() -> None:
    text = DECISION_SRC.read_text(encoding="utf-8")
    assert "tradingagents/agents/schemas.py" in text
    assert "tradingagents/agents/rating.py" in text
    assert "v0.7.0" in text


# ---- models --------------------------------------------------------------


def test_research_plan_requires_all_fields() -> None:
    plan = ResearchPlan(
        recommendation=PortfolioRating.OVERWEIGHT, rationale="r", strategic_actions="a"
    )
    assert plan.recommendation is PortfolioRating.OVERWEIGHT
    with pytest.raises(ValidationError):
        ResearchPlan(recommendation="Buy", rationale="r")  # type: ignore[call-arg]


def test_trader_proposal_optional_fields_default_to_none() -> None:
    p = TraderProposal(action=TraderAction.BUY, reasoning="why")
    assert (p.entry_price, p.stop_loss, p.position_sizing) == (None, None, None)
    with pytest.raises(ValidationError):
        TraderProposal(action="Overweight", reasoning="x")  # type: ignore[arg-type]


def test_portfolio_decision_optional_fields_default_to_none() -> None:
    d = PortfolioDecision(rating="Sell", executive_summary="s", investment_thesis="t")
    assert d.rating is PortfolioRating.SELL
    assert (d.price_target, d.time_horizon) == (None, None)
    with pytest.raises(ValidationError):
        PortfolioDecision(rating="Sell", executive_summary="s")  # type: ignore[call-arg]


# ---- number cleaning -----------------------------------------------------

PLACEHOLDERS = ["", "none", "None", "N/A", "n/a", "na", "null", "nil", "-", "TBD", "unknown", "  "]
CASES: list[tuple[Any, float | None]] = [
    *[(p, None) for p in PLACEHOLDERS],
    ("15%", None),
    ("$1,234.50", 1234.5),
    ("€189.5", 189.5),
    ("£99", 99.0),
    ("¥1,000", 1000.0),
    ("₹2,450.75", 2450.75),  # NSE prices are rupees; deliberate deviation from upstream
    ("189.5", 189.5),
    (" 172.0 ", 172.0),
    ("150-160", None),
    ("around 150", None),
    (189.5, 189.5),
    (150, 150.0),
    (None, None),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_proposal_price_fields_are_cleaned(raw: Any, expected: float | None) -> None:
    p = TraderProposal(action="Buy", reasoning="x", entry_price=raw, stop_loss=raw)
    assert p.entry_price == expected
    assert p.stop_loss == expected


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_decision_price_target_is_cleaned(raw: Any, expected: float | None) -> None:
    d = PortfolioDecision(
        rating="Hold", executive_summary="s", investment_thesis="t", price_target=raw
    )
    assert d.price_target == expected


def test_a_bad_price_does_not_discard_the_rest_of_the_decision() -> None:
    p = TraderProposal(action="Sell", reasoning="keep me", stop_loss="15%", entry_price="$600")
    assert p.stop_loss is None
    assert p.entry_price == 600.0
    assert p.reasoning == "keep me"


# ---- descriptions, schema, round trip ------------------------------------


@pytest.mark.parametrize("model", [ResearchPlan, TraderProposal, PortfolioDecision])
def test_every_field_has_a_description(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert field.description, f"{model.__name__}.{name} has no description"


@pytest.mark.parametrize("model", [ResearchPlan, TraderProposal, PortfolioDecision])
def test_json_schema_builds(model: type[BaseModel]) -> None:
    assert model.model_json_schema()["type"] == "object"


def test_json_round_trip() -> None:
    d = PortfolioDecision(
        rating="Underweight",
        executive_summary="s",
        investment_thesis="t",
        price_target=2450.5,
        time_horizon="3-6 months",
    )
    assert PortfolioDecision.model_validate_json(d.model_dump_json()) == d


# ---- single source of truth (acceptance 2) -------------------------------


def test_no_module_outside_schemas_defines_its_own_ratings() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "schemas" not in p.relative_to(SRC).parts and '"Overweight"' in p.read_text("utf-8")
    ]
    assert offenders == []
