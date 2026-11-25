"""Turn saved runs under results/ into Quartz-ready Markdown under site/content/.

Usage:
    uv run python scripts/export_reports.py [--results-dir results] [--out site/content]
                                            [--runs id1,id2]

Only an allow-list of report files is copied (never state.json, data/, traces or .env).
Every page is scanned for secret-like text first; one hit stops the export and writes nothing.
The home page is rebuilt from all run pages already in the output folder, so exporting one
run at a time keeps earlier runs on the site. Paper trading only; not investment advice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# (source file in the run folder, published page name, page title)
REPORT_PAGES: list[tuple[str, str, str]] = [
    ("market_report.md", "market", "Market report"),
    ("news_report.md", "news", "News report"),
    ("fundamentals_report.md", "fundamentals", "Fundamentals report"),
    ("investment_debate.md", "investment-debate", "Bull and bear debate"),
    ("research_plan.md", "research-plan", "Research plan"),
    ("trader_proposal.md", "trader-proposal", "Trader proposal"),
    ("risk_debate.md", "risk-debate", "Risk debate"),
]
DECISION_FILE = "decision.md"

DISCLAIMER = (
    "**Paper trading only. This is not investment advice.** "
    "These reports were written by AI agents from the data available at the time shown."
)

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}"),
    re.compile(r"\bTA_KITE_(?:API_KEY|ACCESS_TOKEN)\b"),
    re.compile(r"\bANTHROPIC_API_KEY\b\s*[=:]"),
    re.compile(r"\bCURRENTS_API_KEY\b\s*[=:]"),
    re.compile(r"\baccess_token\b\s*[=:]\s*\S+", re.IGNORECASE),
]

ABOUT = f"""---
title: "About"
tags: ["about"]
---

{DISCLAIMER}

## What this is

TradingAgents-IN is a Python rebuild of TradingAgents v0.7.0 for NSE stocks. A team of AI
agents works in this order:

1. Three analysts (market, news, fundamentals) write reports in parallel.
2. A bull and a bear researcher debate; a research manager writes the plan.
3. A trader turns the plan into a proposal.
4. Three risk analysts (aggressive, neutral, conservative) debate the proposal.
5. A portfolio manager gives the final rating on a five-tier scale
   (Buy, Overweight, Hold, Underweight, Sell).

A claim checker lists numbers in the proposal and decision that match no source.

## Honest limits

- Fundamentals are available for current dates only; for past dates they are withheld.
- News reaches back about 30 days.
- A report can be a **gap** (the analyst failed its own checks) or flagged
  **insufficient data**; such pages are published as they are, with a tag.
- Numbers the model derived itself (for example a percentage it computed) can appear in the
  claim-check list as unmatched; that does not always mean they are wrong.
- Rows marked replay are free re-runs of a recorded run, not new analyses.
- Nothing here places, modifies or cancels a real order.
"""


class SecretFoundError(ValueError):
    """A page to publish contains secret-like text. The message never contains the value."""


@dataclass(frozen=True)
class RunSource:
    ticker: str
    date: str
    run_id: str
    path: Path
    meta: dict[str, Any]


def ticker_slug(ticker: str) -> str:
    """URL-safe folder name: `M&M.NS` becomes `MM-NS`."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ticker.replace("&", ""))
    return cleaned.strip("-")


def find_runs(results_dir: Path) -> list[RunSource]:
    found: list[RunSource] = []
    for run_json in sorted(results_dir.glob("*/*/*/run.json")):
        run_dir = run_json.parent
        meta = json.loads(run_json.read_text(encoding="utf-8"))
        found.append(
            RunSource(
                ticker=str(meta["ticker"]),
                date=run_dir.parent.name,
                run_id=str(meta["run_id"]),
                path=run_dir,
                meta=meta,
            )
        )
    return found


def _fm_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if key == "date":
            lines.append(f"date: {value}")  # unquoted ISO date
        else:
            lines.append(f"{key}: {_fm_value(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _strip_h1(text: str) -> str:
    lines = text.lstrip("\n").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).lstrip("\n").rstrip() + "\n"


def _report_tags(base: list[str], body: str) -> list[str]:
    tags = list(base)
    if "**GAP:" in body:
        tags.append("gap")
    if "Insufficient data" in body:
        tags.append("insufficient-data")
    if "withheld" in body.lower():
        tags.append("withheld")
    return tags


def _check_secrets(name: str, text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise SecretFoundError(f"secret-like text found in {name}; export stopped")


def _money(value: float) -> str:
    return f"${value:.4f}"


def _run_pages(run: RunSource) -> dict[str, str]:
    """Return {page file name: full page text} for one run."""
    meta = run.meta
    slug = ticker_slug(run.ticker)
    rating = str(meta.get("rating", "unknown"))
    base_tags = [slug, rating.lower(), "replay" if meta.get("replayed") else "live-run"]
    title = f"{run.ticker} {run.date}: {rating} ({run.run_id})"
    pages: dict[str, str] = {}

    claims = meta.get("claims") or {}
    checked = int(claims.get("checked", 0))
    matched = int(claims.get("matched", 0))
    unmatched = claims.get("unmatched") or []

    body = [DISCLAIMER, ""]
    decision_path = run.path / DECISION_FILE
    body.append("## Final decision\n")
    if decision_path.is_file():
        body.append(_strip_h1(decision_path.read_text(encoding="utf-8")))
    body += [
        "",
        "## Run details\n",
        "| | |",
        "|---|---|",
        f"| Ticker | {run.ticker} |",
        f"| As of | {meta.get('as_of', run.date)} |",
        f"| Cost | {_money(float(meta.get('cost_usd', 0.0)))} |",
        f"| Model calls | {meta.get('llm_calls', 0)} ({meta.get('cached_calls', 0)} cached) |",
        f"| Tokens | {meta.get('input_tokens', 0)} in / {meta.get('output_tokens', 0)} out |",
        f"| Time | {float(meta.get('elapsed_s', 0.0)):.1f} s |",
        f"| Replay of a recorded run | {'yes' if meta.get('replayed') else 'no'} |",
        f"| Number check | {matched} of {checked} numbers matched a source |",
        "",
    ]
    if unmatched:
        body.append("Numbers with no matching source (derived figures may appear here):\n")
        for item in unmatched:
            body.append(f"- {item.get('text', '?')}")
        body.append("")
    body.append("## Reports\n")
    for src_name, page, label in REPORT_PAGES:
        if (run.path / src_name).is_file():
            body.append(f"- [{label}]({page})")

    pages["index.md"] = (
        frontmatter(
            {
                "title": title,
                "date": run.date,
                "tags": base_tags,
                "run_id": run.run_id,
                "ticker": run.ticker,
                "rating": rating,
                "cost_usd": float(meta.get("cost_usd", 0.0)),
                "elapsed_s": round(float(meta.get("elapsed_s", 0.0)), 1),
                "replayed": bool(meta.get("replayed", False)),
                "claims_checked": checked,
                "claims_matched": matched,
            }
        )
        + "\n".join(body)
        + "\n"
    )

    for src_name, page, label in REPORT_PAGES:
        src = run.path / src_name
        if not src.is_file():
            continue
        text = _strip_h1(src.read_text(encoding="utf-8"))
        pages[f"{page}.md"] = (
            frontmatter(
                {
                    "title": f"{label}: {run.ticker} {run.date} ({run.run_id})",
                    "date": run.date,
                    "tags": _report_tags(base_tags, text),
                }
            )
            + f"[Back to the run summary](index)\n\n{DISCLAIMER}\n\n"
            + text
        )
    return pages


def _read_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    head = text[4:].split("\n---\n", 1)[0]
    out: dict[str, Any] = {}
    for line in head.splitlines():
        key, _, raw = line.partition(": ")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def _write_home(out_dir: Path) -> None:
    rows: list[dict[str, Any]] = []
    for index in sorted((out_dir / "runs").glob("*/*/*/index.md")):
        fm = _read_frontmatter(index)
        fm["_link"] = index.parent.relative_to(out_dir).as_posix()
        rows.append(fm)
    rows.sort(key=lambda r: (str(r.get("date")), str(r.get("run_id"))), reverse=True)
    total = sum(float(r.get("cost_usd", 0.0)) for r in rows)
    lines = [
        frontmatter({"title": "TradingAgents-IN reports", "tags": ["home"]}),
        DISCLAIMER,
        "",
        "AI-agent research reports for NSE stocks. Open a run to read the decision and every",
        "agent's report. See [About](about) for how it works and its limits.",
        "",
        "| Date | Ticker | Rating | Cost | Time | Number check | Run |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        replay = " (replay)" if r.get("replayed") else ""
        lines.append(
            f"| {r.get('date')} | {r.get('ticker')} | {r.get('rating')} "
            f"| {_money(float(r.get('cost_usd', 0.0)))} | {r.get('elapsed_s')} s "
            f"| {r.get('claims_matched')}/{r.get('claims_checked')} "
            f"| [{r.get('run_id')}]({r['_link']}){replay} |"
        )
    lines += ["", f"Total cost of the {len(rows)} runs shown: {_money(total)}", ""]
    (out_dir / "index.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def export_reports(results_dir: Path, out_dir: Path, run_ids: list[str] | None = None) -> list[str]:
    """Write the pages for the chosen runs (all when `run_ids` is None). Returns run ids."""
    runs = find_runs(results_dir)
    if run_ids is not None:
        known = {r.run_id for r in runs}
        missing = [rid for rid in run_ids if rid not in known]
        if missing:
            raise ValueError(f"unknown run id(s): {', '.join(missing)}")
        runs = [r for r in runs if r.run_id in run_ids]

    # Build and scan everything first so a leak writes nothing at all.
    planned: dict[Path, str] = {}
    for run in runs:
        run_out = out_dir / "runs" / ticker_slug(run.ticker) / run.date / run.run_id
        for name, text in _run_pages(run).items():
            _check_secrets(f"{run.run_id}/{name}", text)
            planned[run_out / name] = text
    _check_secrets("about.md", ABOUT)

    for path, text in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "about.md").write_text(ABOUT, encoding="utf-8", newline="\n")
    _write_home(out_dir)
    return [r.run_id for r in runs]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--out", type=Path, default=Path("site/content"))
    parser.add_argument("--runs", help="comma-separated run ids to publish (default: all)")
    args = parser.parse_args(argv)
    run_ids = [r.strip() for r in args.runs.split(",") if r.strip()] if args.runs else None
    try:
        exported = export_reports(args.results_dir, args.out, run_ids)
    except (ValueError, OSError) as exc:
        print(f"export_reports: error: {exc}", file=sys.stderr)
        return 2
    print(f"exported {len(exported)} run(s) to {args.out}: {', '.join(exported)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
