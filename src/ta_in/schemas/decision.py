"""Shared decision types: the 5-tier rating scale and the three structured decisions.

Source: TradingAgents v0.7.0 (https://github.com/TauricResearch/TradingAgents, tag v0.7.0)
  - tradingagents/agents/schemas.py : PortfolioRating, TraderAction, ResearchPlan,
    TraderProposal, PortfolioDecision, and the optional-number cleaning
  - tradingagents/agents/rating.py  : RATINGS_5_TIER, RATING_REVIEW

Class names, rating labels and field descriptions are kept identical to upstream so the
baseline comparison (S0.7) sees the same vocabulary. Changes from upstream:
  - type annotations, because mypy runs in strict mode here
  - "₹" is also stripped from price strings; NSE prices are rupees, and upstream strips only
    "$€£¥", which would turn "₹2,450" into None
  - TraderProposal gains `rating` (five-tier, optional) and `evidence` (paths into the run state,
    default empty). Both default to empty so upstream-shaped proposals still validate; the
    trader agent requires them (S4.1)

Every agent imports these types from ta_in.schemas; none may define its own copy.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

# Canonical, ordered 5-tier scale (most bullish to most bearish). [rating.py]
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy",
    "Overweight",
    "Hold",
    "Underweight",
    "Sell",
)

# Signal for a decision with no recognizable rating. It is not a tradeable position: it flags
# output that needs a human/re-run rather than silently degrading to Hold. [rating.py]
RATING_REVIEW = "REVIEW"

# LLMs sometimes write a placeholder string ("None", "N/A", ...) into an optional numeric
# field instead of omitting it. Coerce those to None so the structured call validates
# instead of erroring. Pydantic still parses real numeric strings ("189.5") to float.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}

_CURRENCY_SYMBOLS = "$€£¥₹"  # upstream: "$€£¥"; ₹ added for NSE


def _coerce_optional_float(value: object) -> object:
    """Normalise an LLM-written optional numeric field before validation.

    Three shapes show up in practice: a placeholder string ("None", "N/A") in place of an
    omitted value; a percentage where a price was asked for ("15%"); and a human-formatted
    price ("$1,234.50"). A percentage cannot be salvaged into an absolute level -- reading
    "15%" as 15 would put a stop at 15 on a 600 stock -- so it is dropped like a placeholder,
    leaving one bad field to null out instead of failing the whole proposal. A formatted
    price is reduced to its number.

    Anything that is not a single number is dropped the same way. A range ("150-160") or a
    hedge ("around 150") would otherwise reach pydantic, fail validation, and discard the
    whole decision, losing every field the model got right along with the price.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH_FLOAT or text.endswith("%"):
        return None
    cleaned = text.replace(",", "").lstrip(_CURRENCY_SYMBOLS).strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(StrEnum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(StrEnum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan into a concrete
    transaction proposal: should the desk execute a Buy, a Sell, or sit on Hold this round.
    Position sizing and the nuanced Overweight / Underweight calls happen later at the
    Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view, the rationale
    captures which side of the bull/bear debate carried the argument, and the strategic
    actions translate that into concrete instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Conflicting arguments alone are not a "
            "reason to Hold: commit to the stronger side, sized by how "
            "decisively it wins. Choose Hold only when the evidence is still "
            "balanced after weighing, or too thin to support a call."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including sizing guidance relative to a standard allocation. The "
            "research team does not see the caller's holdings; the trader and "
            "portfolio manager apply the actual position."
        ),
    )


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst reports, then
    turns them into a concrete transaction: what action to take, the reasoning that
    justifies it, and the practical levels for entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(
        default=None,
        description=(
            "Optional entry price target as an absolute number in the instrument's "
            "quote currency (e.g. 189.5), never a percentage or a range. Omit it "
            "if you cannot state a specific level."
        ),
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Optional stop-loss as an absolute price in the instrument's quote "
            "currency (e.g. 172.0), never a percentage. Convert a percentage "
            "distance to the price level it implies, or omit it."
        ),
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    rating: PortfolioRating | None = Field(
        default=None,
        description=(
            "The five-tier view behind the action: Buy or Overweight for a Buy action, "
            "Hold for a Hold action, Underweight or Sell for a Sell action. Required."
        ),
    )
    evidence: list[str] = Field(
        default_factory=list,
        description=(
            "Paths into the run state that support this proposal, taken only from the list "
            "of citable references you were given, e.g. 'plan.recommendation' or "
            "'price_snapshot.last_close'. At least one is required."
        ),
    )

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v: object) -> object:
        return _coerce_optional_float(v)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate extraction pass
    is required. Field descriptions double as the model's output instructions, so the prompt
    body only needs to convey context and the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate. "
            "Conflicting arguments alone are not a reason to Hold: commit to the "
            "stronger side, sized by how decisively it wins. Choose Hold only "
            "when the evidence is still balanced after weighing, or too thin to "
            "support a call."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )

    @field_validator("price_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v: object) -> object:
        return _coerce_optional_float(v)
