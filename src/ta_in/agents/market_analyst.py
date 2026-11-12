"""The market analyst: reads prices and indicators through tools and writes a grounded report.

Grounding (D6): the prompt carries the verified price snapshot, and after the model answers, plain
code checks every `Rs <number>` in the report against the snapshot and against the values the tools
showed. A report with a figure that cannot be traced becomes an explicit gap, never a wrong report.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ta_in.agents.loop import AgentResult, TurnClient, run_agent
from ta_in.agents.market_tools import MAX_INDICATORS, MarketToolkit
from ta_in.data.provider import DataProvider
from ta_in.data.snapshot import PriceSnapshot, render_snapshot
from ta_in.state.agent_state import AgentState
from ta_in.state.models import AgentName, AnalystReport

SYSTEM_PROMPT = f"""You are the market analyst on an equity research team covering NSE stocks.
Your job: study the price action and technical picture of one stock as of the analysis date and
write a report a trader can act on.

How to work:
- Start from the VERIFIED PRICE SNAPSHOT in the user message. It is computed from real data.
- Use get_price_history to look at recent bars, and get_indicator to choose the technical
  indicators that suit this situation (for example trend: sma/ema/macd; momentum: rsi; volatility:
  atr/bollinger bands; volume: vwma). You may use at most {MAX_INDICATORS} different indicators;
  pick the ones that add information and explain why you chose them.
- You only know what the tools and the snapshot show. Do not use outside knowledge of prices.

Grounding rule: every price level you write must be written as "Rs <number>" and must be copied
exactly from the snapshot or from a tool result (rounding to the decimals you show is fine). Do not
estimate, extrapolate or invent a price. If you want to refer to a level you were not shown,
describe it in words instead. Percent moves should be taken from the snapshot returns.

Report: a short market analysis covering trend, momentum, volatility and key levels, finishing with
what the evidence supports (bullish, bearish or neutral) and what would change that view."""

_RS = re.compile(r"(?:Rs\.?|₹|INR)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)


def _decimals(number: str) -> int:
    return len(number.split(".")[1]) if "." in number else 0


def unverified_price_figures(
    text: str, snapshot: PriceSnapshot, seen_values: Iterable[float]
) -> list[str]:
    """Every `Rs <number>` in `text` that matches no snapshot price and no tool-shown value.

    A figure matches a source value if it equals that value rounded to the decimals the figure is
    written with (so "Rs 2,529" matches 2529.0 and "Rs 2480.35" matches 2480.351).
    """
    allowed = [snapshot.last_close, snapshot.high_52w, snapshot.low_52w, *seen_values]
    bad: list[str] = []
    for match in _RS.finditer(text):
        raw = match.group(1)
        figure = float(raw.replace(",", ""))
        places = _decimals(raw)
        if not any(round(v, places) == figure for v in allowed):
            bad.append(match.group(0))
    return bad


def _prompt(state: AgentState, snapshot: PriceSnapshot) -> str:
    lines = [f"Analysis date: {state.as_of.date().isoformat()}", f"Ticker: {state.ticker}"]
    if state.identity is not None:
        sector = state.identity.sector or "sector unknown"
        lines.append(f"Company: {state.identity.name} ({sector}, {state.identity.exchange})")
    lines += ["", render_snapshot(snapshot, state.ticker), "", "Write your market report."]
    return "\n".join(lines)


def _gap(reason: str) -> AnalystReport:
    return AnalystReport(analyst="market", text="", status="gap", gap_reason=reason)


def _report(result: AgentResult, snapshot: PriceSnapshot, toolkit: MarketToolkit) -> AnalystReport:
    if result.stopped_by != "end_turn":
        return _gap(f"market analyst did not finish: stopped by {result.stopped_by}")
    text = result.text.strip()
    if not text:
        return _gap("market analyst returned an empty report")
    bad = unverified_price_figures(text, snapshot, toolkit.seen_values)
    if bad:
        return _gap(f"unverified figures in the report: {', '.join(bad)}")
    return AnalystReport(analyst="market", text=text)


def run_market_analyst(
    client: TurnClient,
    state: AgentState,
    provider: DataProvider,
    *,
    model: str,
    max_steps: int = 12,
) -> AgentState:
    """Run the analyst for `state.ticker` at `state.as_of`; return the state with `market_report`.

    A failed or ungrounded run records a gap report rather than raising. Look-ahead errors and an
    exhausted budget still propagate (they are bugs or hard stops, not analyst failures).
    """
    snapshot = state.price_snapshot
    if snapshot is None:
        raise ValueError("the market analyst needs the price snapshot in the state")
    toolkit = MarketToolkit(provider, state.ticker, state.as_of)
    result = run_agent(
        client,
        agent=AgentName.MARKET_ANALYST,
        model=model,
        system=SYSTEM_PROMPT,
        prompt=_prompt(state, snapshot),
        tools=toolkit.tools(),
        max_steps=max_steps,
    )
    return state.update(
        AgentName.MARKET_ANALYST,
        market_report=_report(result, snapshot, toolkit),
        replace=state.market_report is not None,
    )
