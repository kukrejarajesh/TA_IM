"""The research stage: the bull/bear debate, then the research manager's plan.

This is the one place that fixes which model runs what and how many rounds the debate has, so the
pipeline (`propagate`, S4.5) calls a single function. The debate runs on the quick tier, the manager
on the deep tier. `rounds` is 0 to `MAX_DEBATE_ROUNDS`; 0 skips the debate and the manager judges
the analyst reports directly.

Replay is exact because every prompt is built only from the state, in a fixed order, so a replayed
run asks the cache the same questions as the recorded run (an unrecorded question is a `CacheMiss`).
"""

from __future__ import annotations

from ta_in.agents.research_manager import run_research_manager
from ta_in.agents.researchers import run_investment_debate
from ta_in.llm.structured import StructuredClient
from ta_in.llm.trace import TraceWriter
from ta_in.state.agent_state import AgentState


def run_research_team(
    client: StructuredClient,
    state: AgentState,
    *,
    debate_model: str,
    manager_model: str,
    rounds: int,
    trace: TraceWriter | None = None,
) -> AgentState:
    """Return the state with `investment_debate` (2 turns per round) and `plan` set."""
    state = run_investment_debate(client, state, model=debate_model, rounds=rounds)
    return run_research_manager(client, state, model=manager_model, trace=trace)
