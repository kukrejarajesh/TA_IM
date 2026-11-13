"""Evidence references: paths into the run state that an agent cites as its support.

A reference looks like `price_snapshot.last_close`, `plan.recommendation`, `market_report` or
`investment_debate[1].text`. `resolve_ref` walks the real `AgentState`, so a reference can only
pass if the field exists and holds a value; a made-up field, an out-of-range index, an unset
field, or a report that failed (a `gap`, with nothing to cite) is an `EvidenceRefError`.

Only some fields may be cited by the trader (`TRADER_EVIDENCE_ROOTS`): the inputs and the
research plan, never its own proposal, the risk debate or the decision.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from ta_in.state.agent_state import AgentState
from ta_in.state.models import AnalystReport

# In the order they are listed to the model.
TRADER_EVIDENCE_ROOTS: tuple[str, ...] = (
    "identity",
    "price_snapshot",
    "market_report",
    "news_report",
    "fundamentals_report",
    "investment_debate",
    "plan",
)

_SYNTAX = re.compile(r"^([a-z_]+)((?:\.[A-Za-z_]\w*|\[\d+\])*)$")
_STEP = re.compile(r"\.([A-Za-z_]\w*)|\[(\d+)\]")


class EvidenceRefError(ValueError):
    """A reference does not point to a real, citable value in the state."""


def _fail(ref: str, why: str) -> EvidenceRefError:
    return EvidenceRefError(f"reference {ref!r}: {why}")


def resolve_ref(state: AgentState, ref: str, roots: tuple[str, ...] = TRADER_EVIDENCE_ROOTS) -> Any:
    """Return the value `ref` points to, or raise `EvidenceRefError` saying why not."""
    match = _SYNTAX.match(ref)
    if match is None:
        raise _fail(ref, "not a valid reference (use names, dots and [index], e.g. plan.rationale)")
    root, rest = match.group(1), match.group(2)
    if root not in roots:
        raise _fail(ref, f"{root!r} is not an allowed reference root (allowed: {', '.join(roots)})")
    value: Any = getattr(state, root)
    if value is None:
        raise _fail(ref, f"{root!r} is not set in the state")
    if isinstance(value, AnalystReport) and value.status == "gap":
        raise _fail(ref, f"{root!r} is a gap report ({value.gap_reason}); there is nothing to cite")

    for name, index in _STEP.findall(rest):
        if name:
            if isinstance(value, BaseModel):
                if name not in type(value).model_fields:
                    raise _fail(ref, f"no field {name!r} on {type(value).__name__}")
                value = getattr(value, name)
            elif isinstance(value, dict):
                if name not in value:
                    raise _fail(ref, f"no key {name!r}")
                value = value[name]
            else:
                raise _fail(ref, f"cannot take .{name} of a {type(value).__name__}")
        else:
            if not isinstance(value, list | tuple):
                raise _fail(ref, f"cannot index [{index}] into a {type(value).__name__}")
            if int(index) >= len(value):
                raise _fail(ref, f"index [{index}] is out of range (length {len(value)})")
            value = value[int(index)]
    if value is None:
        raise _fail(ref, "the value is not set")
    return value


def available_refs(state: AgentState, roots: tuple[str, ...] = TRADER_EVIDENCE_ROOTS) -> list[str]:
    """The references that can be cited right now, in a fixed order.

    Each set root is listed; a record (snapshot, identity, plan) also lists its set fields, and a
    list (the debate) lists each element. Reports are listed whole, and a gap report is left out.
    """
    refs: list[str] = []
    for root in roots:
        value = getattr(state, root)
        if value is None or (isinstance(value, list) and not value):
            continue
        if isinstance(value, AnalystReport):
            if value.status == "ok":
                refs.append(root)
            continue
        refs.append(root)
        if isinstance(value, BaseModel):
            refs += [
                f"{root}.{n}" for n in type(value).model_fields if getattr(value, n) is not None
            ]
        elif isinstance(value, list):
            refs += [f"{root}[{i}]" for i in range(len(value))]
    return refs
