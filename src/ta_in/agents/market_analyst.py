"""The market analyst: reads prices and indicators through tools and writes a grounded report.

Grounding (D6): the prompt carries the verified price snapshot, and after the model answers, plain
code checks every `Rs <number>` in the report against the snapshot and against the values the tools
showed (including `calculate` results, whose operands must themselves be shown values). A report
with a figure that cannot be traced becomes an explicit gap, never a wrong report.

Two ways to state a level that was not shown: derive it with `calculate`, or, if it is a judgement
that cannot be computed, wrap it in `<judgement>...</judgement>`. Judgement text is exempt from the
check but is rewritten in the stored report as `[analyst judgement, unverified: ...]`, so every
later agent sees it is opinion. A report whose figures fail the check gets ONE retry that shows the
analyst the rejected report and the exact figures; a second failure is a gap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ta_in.agents.calc import matches_shown
from ta_in.agents.loop import AgentResult, TurnClient, run_agent
from ta_in.agents.market_tools import MAX_INDICATORS, MarketToolkit
from ta_in.data.provider import DataProvider
from ta_in.data.snapshot import PriceSnapshot, render_snapshot
from ta_in.state.agent_state import AgentState
from ta_in.state.models import AgentName, AnalystReport

MAX_JUDGEMENTS = 5

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

Grounding rule: every price level you write must be written as "Rs <number>". Copy it exactly from
the snapshot or from a tool result (rounding to the decimals you show is fine), or derive it with
the calculate tool (for example the gap to a moving average, an ATR-based stop, or the average of
recent closes) and use the number it returns. Never estimate, extrapolate or invent a price, and
never write a derived price without calculating it. Percent moves should be taken from the snapshot
returns. A level that is genuinely your judgement and cannot be computed (a support or resistance
zone, a target) may be written inside <judgement>...</judgement> tags, used sparingly (at most
{MAX_JUDGEMENTS}); readers see it as unverified opinion. Anything else you cannot show, describe in
words.

Report: a short market analysis covering trend, momentum, volatility and key levels, finishing with
what the evidence supports (bullish, bearish or neutral) and what would change that view."""

_RS = re.compile(r"(?:Rs\.?|₹|INR)\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_TAG = re.compile(r"</?judgement>", re.IGNORECASE)


def _decimals(number: str) -> int:
    return len(number.split(".")[1]) if "." in number else 0


def unverified_price_figures(
    text: str, snapshot: PriceSnapshot, seen_values: Iterable[float]
) -> list[str]:
    """Every `Rs <number>` in `text` that matches no snapshot price and no tool-shown value.

    A figure matches a source value if it equals that value rounded or truncated to the decimals
    the figure is written with (so "Rs 2,529" matches 2529.0 and "Rs 2480.35" matches 2480.351).
    """
    allowed = [snapshot.last_close, snapshot.high_52w, snapshot.low_52w, *seen_values]
    bad: list[str] = []
    for match in _RS.finditer(text):
        raw = match.group(1)
        figure = float(raw.replace(",", ""))
        places = _decimals(raw)
        if not any(matches_shown(v, figure, places) for v in allowed):
            bad.append(match.group(0))
    return bad


class _TagError(ValueError):
    """The judgement tags are unclosed, nested, stray or too many."""


def split_judgements(text: str) -> tuple[str, str]:
    """Return (text without judgement spans, text with each span relabelled as unverified opinion).

    Raises `_TagError` for an unclosed, nested or stray tag, or more than `MAX_JUDGEMENTS` spans.
    """
    outside: list[str] = []
    labelled: list[str] = []
    cursor = 0
    open_at: int | None = None
    count = 0
    for tag in _TAG.finditer(text):
        is_open = not tag.group(0).startswith("</")
        if is_open:
            if open_at is not None:
                raise _TagError("nested <judgement> tags")
            outside.append(text[cursor : tag.start()])
            labelled.append(text[cursor : tag.start()])
            open_at = tag.end()
        else:
            if open_at is None:
                raise _TagError("a closing </judgement> tag with no opening tag")
            span = " ".join(text[open_at : tag.start()].split())
            labelled.append(f"[analyst judgement, unverified: {span}]")
            outside.append(" ")
            open_at = None
            count += 1
        cursor = tag.end()
    if open_at is not None:
        raise _TagError("an unclosed <judgement> tag")
    if count > MAX_JUDGEMENTS:
        raise _TagError(f"{count} <judgement> tags (at most {MAX_JUDGEMENTS})")
    outside.append(text[cursor:])
    labelled.append(text[cursor:])
    return "".join(outside), "".join(labelled)


def _prompt(state: AgentState, snapshot: PriceSnapshot) -> str:
    lines = [f"Analysis date: {state.as_of.date().isoformat()}", f"Ticker: {state.ticker}"]
    if state.identity is not None:
        sector = state.identity.sector or "sector unknown"
        lines.append(f"Company: {state.identity.name} ({sector}, {state.identity.exchange})")
    lines += ["", render_snapshot(snapshot, state.ticker), "", "Write your market report."]
    return "\n".join(lines)


def _gap(reason: str) -> AnalystReport:
    return AnalystReport(analyst="market", text="", status="gap", gap_reason=reason)


class _Outcome:
    """A report, plus the failing figures when (and only when) the figure check was the problem."""

    def __init__(self, report: AnalystReport, bad: list[str] | None = None, text: str = "") -> None:
        self.report = report
        self.bad = bad or []
        self.text = text  # the rejected text, shown to the analyst on the retry


def _check(result: AgentResult, snapshot: PriceSnapshot, toolkit: MarketToolkit) -> _Outcome:
    if result.stopped_by != "end_turn":
        return _Outcome(_gap(f"market analyst did not finish: stopped by {result.stopped_by}"))
    text = result.text.strip()
    if not text:
        return _Outcome(_gap("market analyst returned an empty report"))
    try:
        checked, labelled = split_judgements(text)
    except _TagError as err:
        return _Outcome(_gap(f"market analyst misused the judgement tags: {err}"))
    bad = unverified_price_figures(checked, snapshot, toolkit.seen_values)
    if bad:
        return _Outcome(_gap(f"unverified figures in the report: {', '.join(bad)}"), bad, text)
    return _Outcome(AnalystReport(analyst="market", text=labelled))


def _retry_prompt(prompt: str, outcome: _Outcome) -> str:
    return "\n".join(
        [
            prompt,
            "",
            "Your previous report was rejected because these price figures were not found in "
            f"the snapshot or any tool result: {', '.join(outcome.bad)}.",
            "<rejected_report>",
            outcome.text,
            "</rejected_report>",
            "Write a corrected report. Derive any price you need with the calculate tool, mark a "
            "level you cannot compute as <judgement>...</judgement>, or describe it in words.",
        ]
    )


def run_market_analyst(
    client: TurnClient,
    state: AgentState,
    provider: DataProvider,
    *,
    model: str,
    max_steps: int = 12,
) -> AgentState:
    """Run the analyst for `state.ticker` at `state.as_of`; return the state with `market_report`.

    A failed or ungrounded run records a gap report rather than raising. A report whose price
    figures fail the check gets one retry (see the module docstring); every other failure is final.
    Look-ahead errors and an exhausted budget still propagate (bugs or hard stops, not failures).
    """
    snapshot = state.price_snapshot
    if snapshot is None:
        raise ValueError("the market analyst needs the price snapshot in the state")
    toolkit = MarketToolkit(provider, state.ticker, state.as_of, snapshot=snapshot)
    prompt = _prompt(state, snapshot)

    def attempt(text: str) -> _Outcome:
        result = run_agent(
            client,
            agent=AgentName.MARKET_ANALYST,
            model=model,
            system=SYSTEM_PROMPT,
            prompt=text,
            tools=toolkit.tools(),
            max_steps=max_steps,
        )
        return _check(result, snapshot, toolkit)

    outcome = attempt(prompt)
    if outcome.bad:
        first = outcome
        outcome = attempt(_retry_prompt(prompt, first))
        if outcome.bad:
            outcome = _Outcome(
                _gap(
                    f"unverified figures in the report: {', '.join(outcome.bad)} "
                    f"(first attempt: {', '.join(first.bad)})"
                ),
                outcome.bad,
            )
    return state.update(
        AgentName.MARKET_ANALYST,
        market_report=outcome.report,
        replace=state.market_report is not None,
    )
