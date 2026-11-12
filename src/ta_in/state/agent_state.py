"""The typed state every agent reads and writes, with one owner per field.

Agents never mutate the state: `update` and `add_turn` return a new `AgentState` and refuse any
write the agent does not own, so one agent cannot overwrite another's work.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ta_in.data.identity import CompanyIdentity
from ta_in.data.snapshot import PriceSnapshot
from ta_in.schemas import PortfolioDecision, ResearchPlan, TraderProposal
from ta_in.state.models import AgentName, AnalystReport, DebateTurn

DebateName = Literal["investment", "risk"]
_DEBATE_FIELD: dict[DebateName, str] = {"investment": "investment_debate", "risk": "risk_debate"}

# Who may write what. The single source of truth for field ownership.
OWNERSHIP: Mapping[AgentName, frozenset[str]] = {
    AgentName.ORCHESTRATOR: frozenset({"run_id", "ticker", "as_of", "identity", "price_snapshot"}),
    AgentName.MARKET_ANALYST: frozenset({"market_report"}),
    AgentName.NEWS_ANALYST: frozenset({"news_report"}),
    AgentName.FUNDAMENTALS_ANALYST: frozenset({"fundamentals_report"}),
    AgentName.BULL_RESEARCHER: frozenset({"investment_debate"}),
    AgentName.BEAR_RESEARCHER: frozenset({"investment_debate"}),
    AgentName.RESEARCH_MANAGER: frozenset({"plan"}),
    AgentName.TRADER: frozenset({"proposal"}),
    AgentName.AGGRESSIVE_RISK: frozenset({"risk_debate"}),
    AgentName.NEUTRAL_RISK: frozenset({"risk_debate"}),
    AgentName.CONSERVATIVE_RISK: frozenset({"risk_debate"}),
    AgentName.PORTFOLIO_MANAGER: frozenset({"decision"}),
}


class StateWriteError(Exception):
    """An agent tried to write something it does not own, or to overwrite a field already set."""


class AgentState(BaseModel):
    """Everything one run knows. Frozen: change it only through `update` / `add_turn`."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    ticker: str
    as_of: datetime
    identity: CompanyIdentity | None = None
    price_snapshot: PriceSnapshot | None = None
    market_report: AnalystReport | None = None
    news_report: AnalystReport | None = None
    fundamentals_report: AnalystReport | None = None
    investment_debate: list[DebateTurn] = []
    plan: ResearchPlan | None = None
    proposal: TraderProposal | None = None
    risk_debate: list[DebateTurn] = []
    decision: PortfolioDecision | None = None

    @field_validator("as_of")
    @classmethod
    def _as_of_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return v

    # --- writing ---------------------------------------------------------------------

    def update(self, agent: AgentName, *, replace: bool = False, **fields: Any) -> AgentState:
        """Return a new state with `fields` set (owned fields only; set-once unless `replace`)."""
        owned = OWNERSHIP[agent]
        for name in fields:
            if name not in type(self).model_fields:
                raise StateWriteError(f"unknown state field {name!r}")
            if name not in owned:
                raise StateWriteError(f"{agent.value} may not write {name!r}")
            if name in _DEBATE_FIELD.values():
                raise StateWriteError(f"{name!r} is appended to with add_turn, not update")
            if not replace and getattr(self, name) is not None:
                raise StateWriteError(f"{name!r} is already set (pass replace=True to overwrite)")
        return self._with(fields)

    def add_turn(
        self, agent: AgentName, turn: DebateTurn, *, debate: DebateName | None = None
    ) -> AgentState:
        """Return a new state with `turn` appended to the debate `agent` takes part in."""
        if turn.speaker != agent:
            raise StateWriteError(
                f"turn speaker {turn.speaker.value!r} is not the caller {agent.value!r}"
            )
        owned = OWNERSHIP[agent]
        candidates: list[DebateName] = [d for d in _DEBATE_FIELD if _DEBATE_FIELD[d] in owned]
        if debate is None:
            if not candidates:
                raise StateWriteError(f"{agent.value} takes part in no debate")
            debate = candidates[0]
        field = _DEBATE_FIELD[debate]
        if field not in owned:
            raise StateWriteError(f"{agent.value} may not write {field!r}")
        return self._with({field: [*getattr(self, field), turn]})

    def _with(self, changes: Mapping[str, Any]) -> AgentState:
        # Re-validate so a wrongly typed value fails here, not three agents later.
        try:
            return type(self).model_validate({**dict(self), **changes})
        except ValidationError as exc:
            raise ValueError(f"invalid state update: {exc.error_count()} error(s): {exc}") from exc

    # --- serialisation ---------------------------------------------------------------

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> AgentState:
        return cls.model_validate_json(raw)
