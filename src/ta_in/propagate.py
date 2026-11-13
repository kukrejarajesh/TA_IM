"""`propagate(ticker, as_of)`: run the whole pipeline once and report what it cost.

Order: resolve the company identity (an unknown ticker fails here, before any model call), build
the verified price snapshot, run the selected analysts in parallel, then the research team (bull
and bear debate, then the research manager), the trader, the risk team and the portfolio manager.
The analysts, researchers, trader and risk team use the quick model, the two managers the deep one.

Everything the run needs is passed in: the settings, the `LLMClient` (which decides live, record or
replay), the data provider (live or frozen) and an optional `TraceWriter`. Nothing reads "now" or
prints; the caller (the CLI, S5.1) formats the report.

If every selected analyst ends as a `gap` the run stops with `AllAnalystsFailedError`: a decision
with no evidence behind it would be misleading. Fewer gaps continue and stay visible in the reports.
`PointInTimeError`, `BudgetExceeded` and the structured-output errors propagate untouched.

Cost, tokens and calls cover this run only (the records the client added since `propagate` began),
so a client reused for several runs reports each one separately. A replayed run costs nothing:
`cost_usd` is 0.0 and every call is counted as cached.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ta_in.agents.analysts import ANALYST_NAMES, default_specs, run_analysts
from ta_in.agents.portfolio_manager import run_portfolio_manager
from ta_in.agents.research_team import run_research_team
from ta_in.agents.risk_team import run_risk_debate
from ta_in.agents.trader import run_trader
from ta_in.config import Settings
from ta_in.data.provider import DataProvider
from ta_in.data.snapshot import build_snapshot
from ta_in.llm.client import LLMClient
from ta_in.llm.trace import TraceWriter, new_run_id
from ta_in.schemas import PortfolioDecision
from ta_in.state.agent_state import AgentState


class AllAnalystsFailedError(Exception):
    """Every selected analyst ended as a gap, so there is no evidence to decide on."""


class PropagateResult(BaseModel):
    """The final state and decision, plus what the run cost."""

    model_config = ConfigDict(frozen=True)

    state: AgentState
    decision: PortfolioDecision
    run_id: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    llm_calls: int
    cached_calls: int
    elapsed_s: float
    trace_path: Path | None = None


def propagate(
    ticker: str,
    as_of: datetime,
    *,
    settings: Settings,
    client: LLMClient,
    provider: DataProvider,
    trace: TraceWriter | None = None,
    run_id: str | None = None,
    analysts: Sequence[str] = ANALYST_NAMES,
    clock: Callable[[], float] = time.perf_counter,
) -> PropagateResult:
    """Run the pipeline for `ticker` at `as_of` and return the state, decision, cost and time.

    The run id is `run_id` if given, else the trace's, else a new one. Pass the same id to replay a
    recorded run: the id is part of the state, so a replay matches the recording byte for byte only
    with the same id. If both `run_id` and `trace` are given they must agree.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if run_id is not None and trace is not None and run_id != trace.run_id:
        raise ValueError(f"run_id {run_id!r} does not match the trace's run id {trace.run_id!r}")
    started = clock()
    first_record = len(client.records)

    identity = provider.identity(ticker, as_of=as_of)  # unknown ticker: fails before any model call
    snapshot = build_snapshot(provider.prices(identity.ticker, as_of=as_of), as_of=as_of)
    if run_id is None:
        run_id = trace.run_id if trace is not None else new_run_id()
    state = AgentState(
        run_id=run_id,
        ticker=identity.ticker,
        as_of=as_of,
        identity=identity,
        price_snapshot=snapshot,
    )

    quick, deep = settings.quick_model, settings.deep_model
    specs = default_specs(client, provider, model=quick, min_news_items=settings.min_news_items)
    state = run_analysts(state, specs, list(analysts))
    reports = [getattr(state, f"{name}_report") for name in analysts]
    if all(r is not None and r.status == "gap" for r in reports):
        reasons = "; ".join(f"{r.analyst}: {r.gap_reason}" for r in reports if r is not None)
        raise AllAnalystsFailedError(f"every selected analyst failed ({reasons})")

    state = run_research_team(
        client,
        state,
        debate_model=quick,
        manager_model=deep,
        rounds=settings.debate_rounds,
        trace=trace,
    )
    state = run_trader(client, state, model=quick, trace=trace)
    state = run_risk_debate(client, state, model=quick, rounds=settings.risk_rounds)
    state = run_portfolio_manager(client, state, model=deep, trace=trace)
    decision = state.decision
    assert decision is not None  # run_portfolio_manager always sets it or raises

    records = client.records[first_record:]
    return PropagateResult(
        state=state,
        decision=decision,
        run_id=run_id,
        cost_usd=sum(r.cost_usd for r in records),
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        llm_calls=len(records),
        cached_calls=sum(1 for r in records if r.cached),
        elapsed_s=clock() - started,
        trace_path=trace.path if trace is not None else None,
    )
