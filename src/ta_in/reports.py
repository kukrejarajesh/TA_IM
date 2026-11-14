"""Save a finished run: every agent's report as Markdown, the decision as JSON, and an index.

Layout, under a results root (`Settings.results_dir`, default `results/`, git-ignored):

    <root>/<TICKER>/<YYYY-MM-DD>/<run id>/
        market_report.md  news_report.md  fundamentals_report.md
        investment_debate.md  research_plan.md  trader_proposal.md  risk_debate.md  decision.md
        decision.json  state.json  run.json
    <root>/index.json  <root>/index.md        (every saved run with its cost)

The same files are written for every run: a gap report shows a visible GAP section with its reason,
an analyst that was not selected says "not run", and a debate that did not happen says so, so a
missing file never has to be explained. `state.json` is the whole `AgentState` and reloads exactly.

Safety: the run id and ticker become folder names, so both are validated before anything is
written (no separators, no leading dot, no spaces). A run directory is never overwritten. Files are
written to a temp directory that is renamed into place, and the index is updated last with atomic
replaces;
if anything fails the run directory is removed again, so a crash leaves neither a half-written run
nor an index that mentions it. A replay of a run that is already saved writes nothing; a replay of
a recording that was never saved is written and marked `replayed` (cost 0, all calls cached).

This module only writes files: no model, no network, and it never reads the clock.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ta_in.data.point_in_time import IST
from ta_in.propagate import PropagateResult
from ta_in.state.models import AnalystReport, DebateTurn

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_TICKER = re.compile(r"^[A-Za-z0-9&_-]{1,30}(\.[A-Za-z]{1,4})?$")

FILES: tuple[str, ...] = (
    "decision.json",
    "decision.md",
    "fundamentals_report.md",
    "investment_debate.md",
    "market_report.md",
    "news_report.md",
    "research_plan.md",
    "risk_debate.md",
    "run.json",
    "state.json",
    "trader_proposal.md",
)


def validate_run_id(run_id: str) -> str:
    """The run id if it is safe as a folder name, else `ValueError`."""
    if not _RUN_ID.match(run_id):
        raise ValueError(
            f"invalid run id {run_id!r}: use 1 to 80 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return run_id


def validate_ticker(ticker: str) -> str:
    if not _TICKER.match(ticker):
        raise ValueError(f"invalid ticker {ticker!r} for a folder name")
    return ticker


def run_dir(root: Path, ticker: str, as_of: datetime, run_id: str) -> Path:
    """`<root>/<TICKER>/<YYYY-MM-DD>/<run id>/` (the date is the IST date of `as_of`)."""
    day = as_of.astimezone(IST).date().isoformat()
    return root / validate_ticker(ticker) / day / validate_run_id(run_id)


# --- Markdown --------------------------------------------------------------------------


def _head(title: str, result: PropagateResult) -> str:
    state = result.state
    day = state.as_of.astimezone(IST).date().isoformat()
    return f"# {title}\n\n{state.ticker} | {day} | run {result.run_id}\n\n"


def _analyst_md(
    title: str, name: str, report: AnalystReport | None, result: PropagateResult
) -> str:
    head = _head(title, result)
    if report is None:
        return head + f"_Not run: the {name} analyst was not selected for this run._\n"
    if report.status == "gap":
        return (
            head
            + f"> **GAP: the {name} analyst produced no report.**\n"
            + f"> Reason: {report.gap_reason}\n"
        )
    note = ""
    if report.insufficient_data:
        note = (
            "> **Insufficient data:** the analyst flagged the evidence as too thin to analyse.\n\n"
        )
    return head + note + report.text.strip() + "\n"


def _debate_md(title: str, turns: list[DebateTurn], none_text: str, result: PropagateResult) -> str:
    head = _head(title, result)
    if not turns:
        return head + f"{none_text}\n"
    parts = [f"## Round {t.round} - {t.speaker.value}\n\n{t.text.strip()}\n" for t in turns]
    return head + "\n".join(parts)


def _plan_md(result: PropagateResult) -> str:
    plan = result.state.plan
    head = _head("Research plan", result)
    if plan is None:
        return head + "_No plan was produced._\n"
    return (
        head
        + f"## Recommendation\n\n{plan.recommendation.value}\n\n"
        + f"## Rationale\n\n{plan.rationale.strip()}\n\n"
        + f"## Strategic actions\n\n{plan.strategic_actions.strip()}\n"
    )


def _proposal_md(result: PropagateResult) -> str:
    p = result.state.proposal
    head = _head("Trader proposal", result)
    if p is None:
        return head + "_No proposal was produced._\n"
    lines = [f"- **Action:** {p.action.value}"]
    if p.rating is not None:
        lines.append(f"- **Rating:** {p.rating.value}")
    if p.entry_price is not None:
        lines.append(f"- **Entry price:** {p.entry_price}")
    if p.stop_loss is not None:
        lines.append(f"- **Stop loss:** {p.stop_loss}")
    if p.position_sizing:
        lines.append(f"- **Position sizing:** {p.position_sizing}")
    evidence = "\n".join(f"- `{ref}`" for ref in p.evidence) or "_none_"
    return (
        head
        + "\n".join(lines)
        + f"\n\n## Reasoning\n\n{p.reasoning.strip()}\n\n## Evidence\n\n{evidence}\n"
    )


def _decision_md(result: PropagateResult) -> str:
    d = result.decision
    lines = [f"- **Rating:** {d.rating.value}"]
    if d.price_target is not None:
        lines.append(f"- **Price target:** {d.price_target}")
    if d.time_horizon:
        lines.append(f"- **Time horizon:** {d.time_horizon}")
    return (
        _head("Final decision", result)
        + "\n".join(lines)
        + f"\n\n## Executive summary\n\n{d.executive_summary.strip()}\n\n"
        + f"## Investment thesis\n\n{d.investment_thesis.strip()}\n"
    )


def _files(result: PropagateResult) -> dict[str, str]:
    state = result.state
    return {
        "market_report.md": _analyst_md("Market report", "market", state.market_report, result),
        "news_report.md": _analyst_md("News report", "news", state.news_report, result),
        "fundamentals_report.md": _analyst_md(
            "Fundamentals report", "fundamentals", state.fundamentals_report, result
        ),
        "investment_debate.md": _debate_md(
            "Bull and bear debate",
            state.investment_debate,
            "No debate took place (0 rounds).",
            result,
        ),
        "research_plan.md": _plan_md(result),
        "trader_proposal.md": _proposal_md(result),
        "risk_debate.md": _debate_md(
            "Risk team debate", state.risk_debate, "No debate took place (0 rounds).", result
        ),
        "decision.md": _decision_md(result),
        "decision.json": result.decision.model_dump_json(indent=2) + "\n",
        "state.json": state.to_json() + "\n",
    }


# --- the index -------------------------------------------------------------------------


def _load_index(root: Path) -> dict[str, Any]:
    path = root / "index.json"
    if not path.exists():
        return {"runs": [], "total_cost_usd": 0.0}
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
        runs = index["runs"]
        if not isinstance(runs, list):
            raise TypeError("runs is not a list")
    except (ValueError, KeyError, TypeError) as err:
        raise ValueError(
            f"the results index {path} is corrupt ({err}); fix or delete it, nothing was written"
        ) from None
    return {"runs": runs, "total_cost_usd": float(index.get("total_cost_usd", 0.0))}


def _index_md(index: dict[str, Any]) -> str:
    lines = [
        "# Saved runs",
        "",
        "| run id | ticker | date | rating | cost (USD) | calls | cached | time (s) | replay |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in index["runs"]:
        lines.append(
            f"| {e['run_id']} | {e['ticker']} | {e['date']} | {e['rating']} | "
            f"{e['cost_usd']:.4f} | {e['llm_calls']} | {e['cached_calls']} | "
            f"{e['elapsed_s']:.1f} | {'yes' if e['replayed'] else ''} |"
        )
    lines += [
        "",
        f"Total cost: ${index['total_cost_usd']:.4f} over {len(index['runs'])} run(s)",
        "",
    ]
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_index(root: Path, index: dict[str, Any]) -> None:
    index["total_cost_usd"] = sum(e["cost_usd"] for e in index["runs"])
    _atomic_write(root / "index.md", _index_md(index))  # the table first,
    _atomic_write(
        root / "index.json", json.dumps(index, indent=2) + "\n"
    )  # the source of truth last


def _rmdir_empty_parents(path: Path, stop: Path) -> None:
    for parent in path.parents:
        if parent == stop:
            return
        try:
            parent.rmdir()
        except OSError:
            return


# --- save ------------------------------------------------------------------------------


def save_run(result: PropagateResult, root: Path, *, replayed: bool = False) -> Path:
    """Write the run under `root` and add it to the index; return the run directory.

    Raises `ValueError` for an unsafe run id or ticker or a corrupt index (nothing is written),
    `FileExistsError` if the run directory exists (unless `replayed`, which then writes nothing and
    returns it), and re-raises any write error after removing everything it wrote.
    """
    state = result.state
    final = run_dir(root, state.ticker, state.as_of, result.run_id)
    if final.exists():
        if replayed:
            return final
        raise FileExistsError(f"results for run {result.run_id!r} already exist at {final}")
    index = _load_index(root)  # a corrupt index is refused before anything is written
    before = json.loads(json.dumps(index))

    files = _files(result)
    meta = {
        "run_id": result.run_id,
        "ticker": state.ticker,
        "as_of": state.as_of.isoformat(),
        "rating": result.decision.rating.value,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "llm_calls": result.llm_calls,
        "cached_calls": result.cached_calls,
        "elapsed_s": result.elapsed_s,
        "replayed": replayed,
        "files": sorted(FILES),
    }
    files["run.json"] = json.dumps(meta, indent=2) + "\n"

    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".tmp-{result.run_id}-{os.getpid()}"
    try:
        tmp.mkdir()
        for name, text in files.items():
            (tmp / name).write_text(text, encoding="utf-8")
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        _rmdir_empty_parents(final, root)
        raise

    entry = {
        "run_id": result.run_id,
        "ticker": state.ticker,
        "date": state.as_of.astimezone(IST).date().isoformat(),
        "rating": result.decision.rating.value,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "llm_calls": result.llm_calls,
        "cached_calls": result.cached_calls,
        "elapsed_s": result.elapsed_s,
        "replayed": replayed,
        "path": final.relative_to(root).as_posix(),
    }
    index["runs"].append(entry)
    try:
        _write_index(root, index)
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        _rmdir_empty_parents(final, root)
        # Put the table back as it was (the JSON is only replaced last, so it is intact).
        with contextlib.suppress(OSError):
            _atomic_write(root / "index.md", _index_md(before))
        raise
    return final
