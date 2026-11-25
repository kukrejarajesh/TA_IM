"""Tests for scripts/export_reports.py (turns saved runs into Quartz Markdown)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_reports.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_reports", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_reports"] = module
    spec.loader.exec_module(module)
    return module


export = _load()

REPORTS = {
    "market_report.md": "# Market report\n\nINFY.NS | 2026-11-17 | run r1\n\nPrice is Rs 1,130.\n",
    "news_report.md": "# News report\n\nINFY.NS | 2026-11-17 | run r1\n\nSome news [1].\n",
    "fundamentals_report.md": "# Fundamentals report\n\nbody\n",
    "investment_debate.md": "# Investment debate\n\nbody\n",
    "research_plan.md": "# Research plan\n\nbody\n",
    "trader_proposal.md": "# Trader proposal\n\nbody\n",
    "risk_debate.md": "# Risk debate\n\nbody\n",
    "decision.md": "# Final decision\n\nINFY.NS | 2026-11-17 | run r1\n\n- **Rating:** Hold\n",
}


def make_run(
    results: Path,
    ticker: str = "INFY.NS",
    date: str = "2026-11-17",
    run_id: str = "r1",
    rating: str = "Hold",
    overrides: dict[str, str] | None = None,
) -> Path:
    run_dir = results / ticker / date / run_id
    run_dir.mkdir(parents=True)
    files = {**REPORTS, **(overrides or {})}
    for name, text in files.items():
        (run_dir / name).write_text(text, encoding="utf-8")
    run_json = {
        "run_id": run_id,
        "ticker": ticker,
        "as_of": f"{date}T19:06:52+05:30",
        "rating": rating,
        "cost_usd": 0.1,
        "input_tokens": 100,
        "output_tokens": 50,
        "llm_calls": 5,
        "cached_calls": 0,
        "elapsed_s": 12.5,
        "replayed": False,
        "claims": {"checked": 4, "matched": 3, "unmatched": [{"text": "30%"}]},
        "files": sorted(files) + ["state.json"],
    }
    (run_dir / "run.json").write_text(json.dumps(run_json), encoding="utf-8")
    # Files that must never be published.
    (run_dir / "state.json").write_text('{"secret": "state"}', encoding="utf-8")
    (run_dir / "data").mkdir()
    (run_dir / "data" / "manifest.json").write_text("{}", encoding="utf-8")
    return run_dir


def test_exports_pages_and_home(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results)
    exported = export.export_reports(results, out)
    assert exported == ["r1"]
    date_dir = out / "runs" / "INFY-NS" / "2026-11-17"
    for page in (
        "r1.md",
        "r1-market.md",
        "r1-news.md",
        "r1-fundamentals.md",
        "r1-investment-debate.md",
        "r1-research-plan.md",
        "r1-trader-proposal.md",
        "r1-risk-debate.md",
    ):
        assert (date_dir / page).is_file(), page
    assert (out / "index.md").is_file()
    assert (out / "about.md").is_file()
    home = (out / "index.md").read_text(encoding="utf-8")
    assert "r1" in home and "Hold" in home
    assert "Paper trading only" in home


def test_only_allow_listed_files_are_published(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results)
    export.export_reports(results, out)
    names = {p.name for p in out.rglob("*") if p.is_file()}
    assert "state.json" not in names
    assert "manifest.json" not in names
    assert all(n.endswith(".md") for n in names)
    text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.md"))
    assert '"secret"' not in text


def test_ticker_slug_handles_ampersand() -> None:
    assert export.ticker_slug("M&M.NS") == "MM-NS"
    assert export.ticker_slug("INFY.NS") == "INFY-NS"


def test_mm_ticker_page_path(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results, ticker="M&M.NS", run_id="mm1")
    export.export_reports(results, out)
    assert (out / "runs" / "MM-NS" / "2026-11-17" / "mm1.md").is_file()


def test_frontmatter_and_h1_stripped(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results)
    export.export_reports(results, out)
    page = (out / "runs" / "INFY-NS" / "2026-11-17" / "r1-market.md").read_text(encoding="utf-8")
    assert page.startswith("---\n")
    head, _, body = page[4:].partition("\n---\n")
    assert "title:" in head and "tags:" in head and "date: 2026-11-17" in head
    assert "# Market report" not in body
    assert "Price is Rs 1,130." in body


def test_gap_and_insufficient_tags(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(
        results,
        overrides={
            "market_report.md": "# Market report\n\n> **GAP: the analyst produced no report.**\n",
            "news_report.md": "# News report\n\n> **Insufficient data:** too thin.\n",
        },
    )
    export.export_reports(results, out)
    run_dir = out / "runs" / "INFY-NS" / "2026-11-17"
    assert '"gap"' in (run_dir / "r1-market.md").read_text(encoding="utf-8").split("\n---\n")[0]
    assert (
        '"insufficient-data"'
        in (run_dir / "r1-news.md").read_text(encoding="utf-8").split("\n---\n")[0]
    )
    assert (
        '"gap"'
        not in (run_dir / "r1-fundamentals.md").read_text(encoding="utf-8").split("\n---\n")[0]
    )


@pytest.mark.parametrize(
    "leak",
    ["key is sk-ant-api03-abcDEF123", "TA_KITE_ACCESS_TOKEN=abc", "ANTHROPIC_API_KEY: x"],
)
def test_secret_scan_blocks_export_and_writes_nothing(tmp_path: Path, leak: str) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results, overrides={"news_report.md": f"# News\n\n{leak}\n"})
    with pytest.raises(export.SecretFoundError) as err:
        export.export_reports(results, out)
    assert "sk-ant-api03" not in str(err.value)
    assert not out.exists() or not any(out.rglob("*.md"))


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results)
    export.export_reports(results, out)
    first = {p: p.read_text(encoding="utf-8") for p in out.rglob("*.md")}
    export.export_reports(results, out)
    second = {p: p.read_text(encoding="utf-8") for p in out.rglob("*.md")}
    assert first == second


def test_runs_filter_and_incremental_home(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results, run_id="r1")
    make_run(results, ticker="TCS.NS", run_id="r2", rating="Sell")
    assert export.export_reports(results, out, run_ids=["r1"]) == ["r1"]
    assert not (out / "runs" / "TCS-NS").exists()
    assert export.export_reports(results, out, run_ids=["r2"]) == ["r2"]
    home = (out / "index.md").read_text(encoding="utf-8")
    assert "r1" in home and "r2" in home  # earlier run is kept on the home page


def test_unknown_run_id_is_an_error(tmp_path: Path) -> None:
    results = tmp_path / "results"
    make_run(results)
    with pytest.raises(ValueError, match="nope"):
        export.export_reports(results, tmp_path / "content", run_ids=["nope"])


def test_run_page_shows_stats_and_claims(tmp_path: Path) -> None:
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results)
    export.export_reports(results, out)
    page = (out / "runs" / "INFY-NS" / "2026-11-17" / "r1.md").read_text(encoding="utf-8")
    assert "$0.1000" in page
    assert "3 of 4" in page
    assert "[Market report](r1-market)" in page


def _internal_links(text: str) -> list[str]:
    import re

    return [
        t
        for t in re.findall(r"\]\(([^)]+)\)", text)
        if not t.startswith(("#", "http", "/")) and "://" not in t
    ]


def test_no_folder_index_pages_and_links_resolve_beside_the_page(tmp_path: Path) -> None:
    """Regression: a run page that is a folder's index.md loses its folder when the browser
    address has no trailing slash, so its relative links 404 on GitHub Pages. Run pages
    are therefore plain files, and every link between them stays in the same folder."""
    results, out = tmp_path / "results", tmp_path / "content"
    make_run(results)
    export.export_reports(results, out)
    runs = out / "runs"
    assert not list(runs.rglob("index.md"))
    assert [p for p in runs.rglob("*") if p.is_dir() and p.parent.name.startswith("20")] == []
    for page in runs.rglob("*.md"):
        for link in _internal_links(page.read_text(encoding="utf-8")):
            assert "/" not in link, (page.name, link)
            assert (page.parent / f"{link}.md").is_file(), (page.name, link)
    home = (out / "index.md").read_text(encoding="utf-8")
    for link in _internal_links(home):
        if link.startswith("runs/"):
            assert (out / f"{link}.md").is_file(), link
