"""The claim checker: list every number in the proposal and decision that matches no source.

The market and fundamentals analysts check their own figures, but the trader and the portfolio
manager can quote or invent numbers with nothing checking them. This runs once over the FINAL
outputs and compares each number with the sources a reader could verify: the price snapshot (close,
52-week high and low, the four returns) and the numbers in the three analyst reports.

What is a claim: the numeric fields (`entry_price`, `stop_loss`, `price_target`) and every number in
the trader's reasoning and position sizing and in the decision's executive summary and thesis. Not
claims: parts of dates, years, clock times, ratios like 1:2.3, list markers, ordinals, durations
("20-day", "6 months"), multipliers ("0.5x starter", "2x ATR") and bare small integers (0 to 20,
counts such as "three tranches").

A claim matches a source of the same kind (percent with percent, amounts with amounts, after unit
scaling: crore, lakh, million, billion, ...) when it equals the source rounded or truncated at the
decimals written, or is within `rel_tol` (default 0.5%) of it. Signs are ignored.

This only REPORTS: it never fails a run. Derived numbers (a difference, a percent computed from two
shown values) are legitimate but cannot be matched, so they appear in the list with their context
for a human to judge. Numbers in the research plan or the debates are not claims, and the analysts
keep their own stricter checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ta_in.agents.calc import matches_shown
from ta_in.state.agent_state import AgentState
from ta_in.state.models import AnalystReport

DEFAULT_REL_TOL = 0.005
CONTEXT_CHARS = 30

Kind = Literal["amount", "percent"]

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_NOISE_SPANS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b(?:,?\s+\d{{4}})?", re.IGNORECASE),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?", re.IGNORECASE),
    re.compile(rf"\b{_MONTH}\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?"),
    re.compile(r"^\s*\d{1,3}[.)]", re.MULTILINE),
    re.compile(r"\(\d{1,3}\)"),
]
_ORDINAL = re.compile(r"^(?:st|nd|rd|th)\b", re.IGNORECASE)
_MULTIPLIER = re.compile(r"^(?:x\b|×)", re.IGNORECASE)  # "0.5x starter", "2×ATR"
_DURATION = re.compile(
    r"^\s*-?\s*(?:trading\s+)?(?:days?|weeks?|months?|years?|yrs?|sessions?|bars?|hours?|"
    r"minutes?)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    r"""
    (?<![A-Za-z0-9_.,])
    (?P<cur>(?:Rs\.?|₹|INR)\s*)?
    (?P<sign>(?<![^\s(])[+\-−])?
    (?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?P<unit>
        \s*(?:%|percent\b|per\s?cent\b|lakh\s+crore\b|crore\b|lakh\b|million\b|billion\b
            |trillion\b|thousand\b|cr\b|bn\b|mn\b)
        |[kKmM]\b
    )?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_SCALES = {
    "lakh crore": 1e12,
    "crore": 1e7,
    "cr": 1e7,
    "lakh": 1e5,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "bn": 1e9,
    "trillion": 1e12,
    "thousand": 1e3,
    "k": 1e3,
}


@dataclass(frozen=True)
class Number:
    """One number found in text. `value` is scaled by its unit (crore, lakh, ...); `raw` is not."""

    text: str
    raw: float
    value: float
    scale: float
    places: int
    kind: Kind
    start: int
    end: int


class UnmatchedClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    where: str
    text: str
    value: float
    context: str


class ClaimReport(BaseModel):
    """How many numbers were checked, and the ones that match no source."""

    model_config = ConfigDict(frozen=True)

    checked: int
    matched: int
    unmatched: list[UnmatchedClaim]

    def render(self) -> str:
        if self.checked == 0:
            return "Number check: no numbers to check"
        if not self.unmatched:
            return f"Number check: all {self.checked} numbers match a source"
        lines = [
            f"Number check: {len(self.unmatched)} of {self.checked} numbers match no source "
            "(derived figures may appear here)"
        ]
        lines += [f"  - {u.where}: {u.text}   ...{u.context}..." for u in self.unmatched]
        return "\n".join(lines)


def _noise_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for pattern in _NOISE_SPANS for m in pattern.finditer(text)]


def _is_noise(
    n: Number, text: str, spans: list[tuple[int, int]], *, plain: bool, comma: bool
) -> bool:
    if any(start <= n.start < end for start, end in spans):
        return True
    after = text[n.end :]
    if _ORDINAL.match(after) or _DURATION.match(after) or _MULTIPLIER.match(after):
        return True
    if not plain:  # a currency sign, a percent or a unit makes it a real claim
        return False
    if n.places == 0 and not comma and 1900 <= abs(n.raw) <= 2100:
        return True  # a bare year
    return n.places == 0 and not comma and 0 <= abs(n.raw) <= 20  # a count such as "3 tranches"


def extract_numbers(text: str, *, skip_noise: bool = True) -> list[Number]:
    """The numbers in `text`; with `skip_noise` the not-a-claim kinds listed above are left out."""
    spans = _noise_spans(text) if skip_noise else []
    found: list[Number] = []
    for m in _NUMBER.finditer(text):
        num = m.group("num")
        unit = (m.group("unit") or "").strip().lower()
        kind: Kind = "percent" if unit.startswith(("%", "percent", "per")) else "amount"
        scale = 1.0 if kind == "percent" else _SCALES.get(" ".join(unit.split()), 1.0)
        raw = float(num.replace(",", ""))
        if (m.group("sign") or "") in ("-", "−"):
            raw = -raw
        number = Number(
            text=m.group(0).strip(),
            raw=raw,
            value=raw * scale,
            scale=scale,
            places=len(num.split(".")[1]) if "." in num else 0,
            kind=kind,
            start=m.start(),
            end=m.end(),
        )
        plain = m.group("cur") is None and not unit
        if skip_noise and _is_noise(number, text, spans, plain=plain, comma="," in num):
            continue
        found.append(number)
    return found


def _source_numbers(state: AgentState) -> list[Number]:
    sources: list[Number] = []

    def add(value: float, places: int, kind: Kind) -> None:
        sources.append(Number("snapshot", value, value, 1.0, places, kind, 0, 0))

    snap = state.price_snapshot
    if snap is not None:
        for price in (snap.last_close, snap.high_52w, snap.low_52w):
            add(price, 2, "amount")
        for fraction in (snap.return_1d, snap.return_5d, snap.return_20d, snap.return_60d):
            if fraction is not None:
                add(fraction * 100, 2, "percent")
    reports: list[AnalystReport | None] = [
        state.market_report,
        state.news_report,
        state.fundamentals_report,
    ]
    for report in reports:
        if report is not None and report.status == "ok":
            sources += extract_numbers(report.text, skip_noise=False)
    return sources


def _matches(claim: Number, source: Number, rel_tol: float) -> bool:
    if claim.kind != source.kind:
        return False
    if claim.scale == source.scale and matches_shown(abs(source.raw), abs(claim.raw), claim.places):
        return True
    a, b = abs(claim.value), abs(source.value)
    if b == 0:
        return a == 0
    return abs(a - b) / b <= rel_tol


def _context(text: str, n: Number) -> str:
    window = text[max(0, n.start - CONTEXT_CHARS) : n.end + CONTEXT_CHARS]
    return " ".join(window.split())


def _field_number(value: float) -> Number:
    shown = str(value)
    places = len(shown.split(".")[1]) if "." in shown else 0
    return Number(shown, value, value, 1.0, places, "amount", 0, 0)


def check_claims(state: AgentState, *, rel_tol: float = DEFAULT_REL_TOL) -> ClaimReport:
    """Check the proposal's and the decision's numbers against the snapshot and the reports."""
    proposal, decision = state.proposal, state.decision
    if proposal is None:
        raise ValueError("the claim checker needs the trader's proposal (state.proposal)")
    if decision is None:
        raise ValueError("the claim checker needs the final decision (state.decision)")

    claims: list[tuple[str, int, Number, str]] = []  # (where, position, number, context)
    fields: list[tuple[str, float | None]] = [
        ("proposal.entry_price", proposal.entry_price),
        ("proposal.stop_loss", proposal.stop_loss),
        ("decision.price_target", decision.price_target),
    ]
    for where, value in fields:
        if value is not None:
            number = _field_number(value)
            claims.append((where, 0, number, f"{where.split('.')[1]} = {value}"))
    texts: list[tuple[str, str | None]] = [
        ("proposal.reasoning", proposal.reasoning),
        ("proposal.position_sizing", proposal.position_sizing),
        ("decision.executive_summary", decision.executive_summary),
        ("decision.investment_thesis", decision.investment_thesis),
    ]
    for where, text in texts:
        if text:
            for number in extract_numbers(text):
                claims.append((where, number.start, number, _context(text, number)))

    sources = _source_numbers(state)
    unmatched: list[UnmatchedClaim] = []
    for where, _position, number, context in sorted(claims, key=lambda c: (c[0], c[1])):
        if not any(_matches(number, source, rel_tol) for source in sources):
            unmatched.append(
                UnmatchedClaim(where=where, text=number.text, value=number.value, context=context)
            )
    return ClaimReport(
        checked=len(claims), matched=len(claims) - len(unmatched), unmatched=unmatched
    )
