"""Small records that live inside `AgentState`: who is writing, analyst reports, debate turns."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentName(StrEnum):
    """Every writer of the shared state. `ORCHESTRATOR` sets the run inputs; the rest are agents."""

    ORCHESTRATOR = "orchestrator"
    MARKET_ANALYST = "market_analyst"
    NEWS_ANALYST = "news_analyst"
    FUNDAMENTALS_ANALYST = "fundamentals_analyst"
    BULL_RESEARCHER = "bull_researcher"
    BEAR_RESEARCHER = "bear_researcher"
    RESEARCH_MANAGER = "research_manager"
    TRADER = "trader"
    AGGRESSIVE_RISK = "aggressive_risk"
    NEUTRAL_RISK = "neutral_risk"
    CONSERVATIVE_RISK = "conservative_risk"
    PORTFOLIO_MANAGER = "portfolio_manager"


class AnalystReport(BaseModel):
    """One analyst's output. A failed analyst leaves an explicit `gap`, never a missing field.

    `insufficient_data` flags thin evidence (S2.7): the analyst says so instead of analysing it.
    """

    model_config = ConfigDict(frozen=True)

    analyst: Literal["market", "news", "fundamentals"]
    text: str
    status: Literal["ok", "gap"] = "ok"
    gap_reason: str | None = None
    insufficient_data: bool = False

    @model_validator(mode="after")
    def _gap_needs_a_reason(self) -> AnalystReport:
        if self.status == "gap" and not self.gap_reason:
            raise ValueError("a gap report must say why (gap_reason)")
        return self


class DebateTurn(BaseModel):
    """One argument in a debate (bull/bear, or the three risk analysts)."""

    model_config = ConfigDict(frozen=True)

    speaker: AgentName
    round: int = Field(ge=1)
    text: str
