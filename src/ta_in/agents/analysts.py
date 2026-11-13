"""Run the selected analysts in parallel; a failed analyst leaves an explicit gap, not a crash.

Each analyst is an `AnalystSpec`: a name and a function that takes the run's `AgentState` and
returns it with that analyst's report set. `run_analysts` runs the selected ones in threads (the
work is blocking API calls), then merges each analyst's own report field into one state, in the
order the caller selected them, so the result does not depend on who finished first.

Ordinary failures become a `gap` report with the reason. `PointInTimeError` (a look-ahead bug) and
`BudgetExceeded` (a hard stop) are NOT hidden as gaps: they are raised once every analyst has
finished, so no thread is left running.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ta_in.agents.fundamentals_analyst import run_fundamentals_analyst
from ta_in.agents.loop import TurnClient
from ta_in.agents.market_analyst import run_market_analyst
from ta_in.agents.news_analyst import run_news_analyst
from ta_in.data.point_in_time import PointInTimeError
from ta_in.data.provider import DataProvider
from ta_in.llm.client import BudgetExceeded
from ta_in.state.agent_state import AgentState
from ta_in.state.models import AgentName, AnalystReport

# Every analyst the system will have: name -> (who writes, which state field).
_ANALYSTS: dict[str, tuple[AgentName, str]] = {
    "market": (AgentName.MARKET_ANALYST, "market_report"),
    "news": (AgentName.NEWS_ANALYST, "news_report"),
    "fundamentals": (AgentName.FUNDAMENTALS_ANALYST, "fundamentals_report"),
}
ANALYST_NAMES: tuple[str, ...] = tuple(_ANALYSTS)

_FATAL = (PointInTimeError, BudgetExceeded)
_REASON_LIMIT = 300

AnalystRunner = Callable[[AgentState], AgentState]


@dataclass(frozen=True)
class AnalystSpec:
    """One runnable analyst. `run` returns the state with this analyst's report field set."""

    name: str
    run: AnalystRunner


def market_spec(client: TurnClient, provider: DataProvider, *, model: str) -> AnalystSpec:
    return AnalystSpec(
        name="market",
        run=lambda state: run_market_analyst(client, state, provider, model=model),
    )


def news_spec(
    client: TurnClient, provider: DataProvider, *, model: str, min_news_items: int
) -> AnalystSpec:
    return AnalystSpec(
        name="news",
        run=lambda state: run_news_analyst(
            client, state, provider, model=model, min_news_items=min_news_items
        ),
    )


def fundamentals_spec(client: TurnClient, provider: DataProvider, *, model: str) -> AnalystSpec:
    return AnalystSpec(
        name="fundamentals",
        run=lambda state: run_fundamentals_analyst(client, state, provider, model=model),
    )


def default_specs(
    client: TurnClient, provider: DataProvider, *, model: str, min_news_items: int
) -> dict[str, AnalystSpec]:
    """All three analysts. Pass `Settings.min_news_items` as `min_news_items`."""
    return {
        "market": market_spec(client, provider, model=model),
        "news": news_spec(client, provider, model=model, min_news_items=min_news_items),
        "fundamentals": fundamentals_spec(client, provider, model=model),
    }


def parse_analysts(text: str, available: Iterable[str]) -> list[str]:
    """Turn "market,news" into `["market", "news"]` (the future `--analysts` flag).

    Spaces, case and repeats are tolerated (first mention wins). Empty input, an unknown name, or
    a known analyst that is not available yet raises `ValueError` saying what is valid.
    """
    have = list(available)
    names: list[str] = []
    for part in text.split(","):
        name = part.strip().lower()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ValueError(f"no analysts given; choose from: {', '.join(ANALYST_NAMES)}")
    for name in names:
        if name not in _ANALYSTS:
            raise ValueError(f"unknown analyst {name!r}; choose from: {', '.join(ANALYST_NAMES)}")
        if name not in have:
            raise ValueError(f"analyst {name!r} is not available yet; available: {', '.join(have)}")
    return names


def _gap(name: str, reason: str) -> AnalystReport:
    return AnalystReport(
        analyst=name,  # type: ignore[arg-type]
        text="",
        status="gap",
        gap_reason=reason[:_REASON_LIMIT],
    )


def run_analysts(
    state: AgentState,
    specs: Mapping[str, AnalystSpec],
    selected: Sequence[str],
    *,
    max_workers: int | None = None,
) -> AgentState:
    """Run only the `selected` analysts concurrently and return `state` with their reports.

    Analysts that are not selected leave their field `None`. Each analyst starts from the same
    input `state`; only its own report field is taken from what it returns.
    """
    if not selected:
        raise ValueError("select at least one analyst")
    missing = [n for n in selected if n not in specs or n not in _ANALYSTS]
    if missing:
        raise ValueError(
            f"no analyst available for: {', '.join(missing)}; available: {', '.join(specs)}"
        )

    futures: dict[str, Future[AgentState]] = {}
    with ThreadPoolExecutor(max_workers=max_workers or len(selected)) as pool:
        for name in selected:
            futures[name] = pool.submit(specs[name].run, state)
        reports: dict[str, AnalystReport] = {}
        fatal: BaseException | None = None
        for name in selected:  # in selection order; result() waits, so all finish before we raise
            agent, field = _ANALYSTS[name]
            try:
                finished = futures[name].result()
            except _FATAL as err:
                fatal = fatal or err
                continue
            except Exception as err:
                reports[name] = _gap(name, f"{type(err).__name__}: {err}")
                continue
            report = getattr(finished, field)
            reports[name] = report or _gap(name, f"{agent.value} wrote no report")
    if fatal is not None:
        raise fatal

    merged = state
    for name in selected:
        agent, field = _ANALYSTS[name]
        fields: dict[str, Any] = {
            field: reports[name]
        }  # Any: keeps mypy from reading it as `replace`
        merged = merged.update(agent, replace=getattr(merged, field) is not None, **fields)
    return merged
