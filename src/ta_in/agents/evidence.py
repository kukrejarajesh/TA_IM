"""The insufficient-data rule: thin evidence is flagged, not analysed.

An analyst that would have to reason from too little evidence says so instead. The thin result is
returned before the model is ever called, so no cost is spent and no analysis is invented. The
report is `status="ok"` with `insufficient_data=True`: nothing failed, the evidence was thin, and
agents further down the pipeline read the flag.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Literal, cast

from ta_in.state.models import AnalystReport

AnalystKind = Literal["market", "news", "fundamentals"]


def is_thin(found: int, minimum: int) -> bool:
    """True when `found` is below `minimum`. Exactly the minimum is enough."""
    return found < minimum


def insufficient_data_report(
    analyst: str, found: int, minimum: int, what: str = "news items"
) -> AnalystReport:
    """The report an analyst returns instead of analysing too little evidence."""
    noun = what.removesuffix("s") if found == 1 else what
    return AnalystReport(
        analyst=cast(AnalystKind, analyst),
        text=(
            f"Insufficient data: found {found} {noun}, the minimum is {minimum}. "
            f"No analysis was made."
        ),
        insufficient_data=True,
    )


def run_gated(
    analyst: str,
    items: Iterable[object],
    minimum: int,
    analyse: Callable[[], AnalystReport],
    what: str = "news items",
) -> AnalystReport:
    """Count `items`; if fewer than `minimum`, return the insufficient-data report WITHOUT
    calling `analyse`; otherwise return whatever `analyse()` returns.

    `items` should already be what the analyst may use: filtered to `as_of` and de-duplicated.
    """
    found = sum(1 for _ in items)
    if is_thin(found, minimum):
        return insufficient_data_report(analyst, found, minimum, what)
    return analyse()
