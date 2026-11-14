"""S5.2: every run is saved as Markdown and JSON under results/<ticker>/<date>/<run id>/ (offline).

Runs the real pipeline on a frozen cell with the scripted SDK, then saves it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pipeline_sdk import ForbiddenSDK, PipelineSDK

from ta_in import reports
from ta_in.config import Settings
from ta_in.fixtures import FrozenDataProvider, Manifest
from ta_in.llm import CacheMode, LLMClient, ResponseCache
from ta_in.propagate import PropagateResult, propagate
from ta_in.reports import save_run
from ta_in.schemas import PortfolioDecision
from ta_in.state import AgentState

FIXTURES = Path(__file__).parent.parent / "fixtures"
FILES = {
    "market_report.md",
    "news_report.md",
    "fundamentals_report.md",
    "investment_debate.md",
    "research_plan.md",
    "trader_proposal.md",
    "risk_debate.md",
    "decision.md",
    "decision.json",
    "state.json",
    "run.json",
}


def _cells() -> list[Any]:
    return list(Manifest.model_validate_json((FIXTURES / "manifest.json").read_text("utf-8")).cells)


def _settings(debate: int = 1, risk: int = 1) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        ANTHROPIC_API_KEY="sk-test-key",
        budget_usd=1.0,
        debate_rounds=debate,
        risk_rounds=risk,
    )


def _client(tmp_path: Path, mode: CacheMode, sdk: Any) -> LLMClient:
    return LLMClient(
        _settings(),
        sdk=sdk,
        sleep=lambda _s: None,
        cache=ResponseCache(tmp_path / "cache"),
        cache_mode=mode,
    )


def _run(
    tmp_path: Path,
    *,
    ticker: str = "RELIANCE.NS",
    run_id: str = "run-1",
    sdk: Any = None,
    mode: CacheMode = CacheMode.OFF,
    debate: int = 1,
    risk: int = 1,
    **kw: Any,
) -> PropagateResult:
    cell = next(c for c in _cells() if c.ticker == ticker)
    return propagate(
        ticker,
        cell.as_of,
        settings=_settings(debate, risk),
        client=_client(tmp_path, mode, sdk or PipelineSDK()),
        provider=FrozenDataProvider(FIXTURES),
        run_id=run_id,
        **kw,
    )


def _dir(root: Path, result: PropagateResult) -> Path:
    day = result.state.as_of.date().isoformat()
    return root / result.state.ticker / day / result.run_id


def _read(path: Path) -> str:
    return path.read_text("utf-8")


# --- criterion 1: every run writes the full set of files -------------------------------


def test_a_run_writes_the_full_set_of_files_under_ticker_date_run_id(tmp_path: Path) -> None:
    result = _run(tmp_path)
    out = save_run(result, tmp_path / "results")

    assert out == _dir(tmp_path / "results", result)
    assert out.parts[-3] == "RELIANCE.NS" and out.parts[-1] == "run-1"
    assert {p.name for p in out.iterdir()} == FILES
    assert all(p.stat().st_size > 0 for p in out.iterdir())


def test_the_markdown_files_hold_the_texts_of_every_agent(tmp_path: Path) -> None:
    result = _run(tmp_path)
    state = result.state
    out = save_run(result, tmp_path / "results")

    assert state.market_report is not None and state.news_report is not None
    assert state.market_report.text in _read(out / "market_report.md")
    assert state.news_report.text in _read(out / "news_report.md")
    assert state.fundamentals_report is not None
    assert state.fundamentals_report.text in _read(out / "fundamentals_report.md")
    assert "insufficient" in _read(out / "fundamentals_report.md").lower()
    debate = _read(out / "investment_debate.md")
    for turn in state.investment_debate:
        assert turn.text in debate and f"Round {turn.round}" in debate
    risk = _read(out / "risk_debate.md")
    for turn in state.risk_debate:
        assert turn.text in risk and turn.speaker.value in risk
    assert state.plan is not None and state.proposal is not None
    plan = _read(out / "research_plan.md")
    assert state.plan.rationale in plan and state.plan.recommendation.value in plan
    proposal = _read(out / "trader_proposal.md")
    assert state.proposal.reasoning in proposal and state.proposal.action.value in proposal
    for ref in state.proposal.evidence:
        assert ref in proposal
    decision = _read(out / "decision.md")
    assert result.decision.executive_summary in decision
    assert result.decision.investment_thesis in decision
    for name in ("market_report.md", "news_report.md"):
        assert "RELIANCE.NS" in _read(out / name) and "run-1" in _read(out / name)


def test_the_json_files_load_back_exactly(tmp_path: Path) -> None:
    result = _run(tmp_path)
    out = save_run(result, tmp_path / "results")

    decision = PortfolioDecision.model_validate_json(_read(out / "decision.json"))
    assert decision == result.decision
    assert AgentState.from_json(_read(out / "state.json")) == result.state
    meta = json.loads(_read(out / "run.json"))
    assert meta["run_id"] == "run-1" and meta["ticker"] == "RELIANCE.NS"
    assert meta["rating"] == result.decision.rating.value
    assert meta["cost_usd"] == pytest.approx(result.cost_usd) and meta["cost_usd"] > 0
    assert meta["input_tokens"] == result.input_tokens
    assert meta["output_tokens"] == result.output_tokens
    assert meta["llm_calls"] == result.llm_calls == 11
    assert meta["cached_calls"] == 0 and meta["replayed"] is False
    assert meta["elapsed_s"] == pytest.approx(result.elapsed_s)
    assert set(meta["files"]) == FILES


def test_an_analyst_that_did_not_run_is_stated_and_the_file_set_is_unchanged(
    tmp_path: Path,
) -> None:
    out = save_run(_run(tmp_path, analysts=("market", "news")), tmp_path / "results")
    assert {p.name for p in out.iterdir()} == FILES
    assert "not run" in _read(out / "fundamentals_report.md").lower()


def test_no_debate_is_stated_when_the_rounds_are_zero(tmp_path: Path) -> None:
    out = save_run(_run(tmp_path, debate=0, risk=0), tmp_path / "results")
    assert {p.name for p in out.iterdir()} == FILES
    assert "no debate" in _read(out / "investment_debate.md").lower()
    assert "no debate" in _read(out / "risk_debate.md").lower()


def test_a_gap_report_is_shown_as_a_gap_with_its_reason(tmp_path: Path) -> None:
    result = _run(tmp_path, sdk=PipelineSDK(failing=("news",)), analysts=("market", "news"))
    out = save_run(result, tmp_path / "results")
    text = _read(out / "news_report.md")
    assert "GAP" in text and "news is down" in text
    assert {p.name for p in out.iterdir()} == FILES


# --- criterion 2: an index lists runs with their cost ----------------------------------


def test_the_index_lists_the_run_with_its_cost(tmp_path: Path) -> None:
    root = tmp_path / "results"
    result = _run(tmp_path)
    save_run(result, root)

    index = json.loads(_read(root / "index.json"))
    assert len(index["runs"]) == 1
    entry = index["runs"][0]
    assert entry["run_id"] == "run-1" and entry["ticker"] == "RELIANCE.NS"
    assert entry["date"] == result.state.as_of.date().isoformat()
    assert entry["rating"] == result.decision.rating.value
    assert entry["cost_usd"] == pytest.approx(result.cost_usd)
    assert entry["llm_calls"] == 11 and entry["replayed"] is False
    assert Path(entry["path"]).as_posix() == f"RELIANCE.NS/{entry['date']}/run-1"
    table = _read(root / "index.md")
    assert "run-1" in table and "RELIANCE.NS" in table and f"{result.cost_usd:.4f}" in table


def test_several_runs_accumulate_and_the_total_adds_up(tmp_path: Path) -> None:
    root = tmp_path / "results"
    results = [
        _run(tmp_path, ticker="RELIANCE.NS", run_id="a"),
        _run(tmp_path, ticker="INFY.NS", run_id="b"),
        _run(tmp_path, ticker="RELIANCE.NS", run_id="c", debate=0, risk=0),
    ]
    for result in results:
        save_run(result, root)

    index = json.loads(_read(root / "index.json"))
    assert [(e["run_id"], e["ticker"]) for e in index["runs"]] == [
        ("a", "RELIANCE.NS"),
        ("b", "INFY.NS"),
        ("c", "RELIANCE.NS"),
    ]
    total = sum(r.cost_usd for r in results)
    assert index["total_cost_usd"] == pytest.approx(total)
    table = _read(root / "index.md")
    assert table.count("\n| ") >= 3 and f"{total:.4f}" in table
    for run_id in ("a", "b", "c"):
        assert run_id in table


def test_saving_the_same_run_twice_does_not_duplicate_it(tmp_path: Path) -> None:
    root = tmp_path / "results"
    result = _run(tmp_path)
    save_run(result, root)
    with pytest.raises(FileExistsError):
        save_run(result, root)
    assert len(json.loads(_read(root / "index.json"))["runs"]) == 1


def test_the_index_markdown_is_rebuilt_from_the_json_every_time(tmp_path: Path) -> None:
    root = tmp_path / "results"
    save_run(_run(tmp_path, run_id="a"), root)
    first = _read(root / "index.md")
    save_run(_run(tmp_path, run_id="b"), root)
    second = _read(root / "index.md")
    assert first != second and "a" in second and "b" in second


# --- safety ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["../x", "a/b", "a\\b", ".", "..", "", "x" * 81, "has space", ".hidden", "a:b", "ü"]
)
def test_an_unsafe_run_id_is_refused_before_anything_is_written(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "results"
    result = _run(tmp_path).model_copy(update={"run_id": bad})
    with pytest.raises(ValueError, match="run id"):
        save_run(result, root)
    assert not root.exists()


def test_an_unsafe_ticker_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    root = tmp_path / "results"
    result = _run(tmp_path)
    bad_state = result.state.model_copy(update={"ticker": "../EVIL"})
    with pytest.raises(ValueError, match="ticker"):
        save_run(result.model_copy(update={"state": bad_state}), root)
    assert not root.exists()


def test_a_crash_while_writing_files_leaves_no_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "results"
    result = _run(tmp_path)
    original = Path.write_text
    calls = {"n": 0}

    def flaky(self: Path, *args: Any, **kwargs: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError("disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(OSError, match="disk full"):
        save_run(result, root)
    monkeypatch.undo()

    assert not _dir(root, result).exists()
    leftovers = [p for p in root.rglob("*") if p.is_file()]
    assert leftovers == []  # no half-written run and no temp files


def test_a_crash_while_updating_the_index_rolls_the_run_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "results"
    save_run(_run(tmp_path, run_id="good"), root)
    before = _read(root / "index.json")
    second = _run(tmp_path, run_id="bad")
    real_replace = os.replace

    def failing(src: Any, dst: Any) -> None:
        if str(dst).endswith("index.json"):
            raise OSError("cannot replace the index")
        real_replace(src, dst)

    monkeypatch.setattr(reports.os, "replace", failing)
    with pytest.raises(OSError, match="index"):
        save_run(second, root)
    monkeypatch.undo()

    assert not _dir(root, second).exists()
    assert _read(root / "index.json") == before  # still valid and unchanged
    json.loads(_read(root / "index.json"))


def test_a_corrupt_index_is_refused_not_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "results"
    root.mkdir()
    (root / "index.json").write_text("{not json", encoding="utf-8")
    result = _run(tmp_path)
    with pytest.raises(ValueError, match="index"):
        save_run(result, root)
    assert (root / "index.json").read_text("utf-8") == "{not json"
    assert not _dir(root, result).exists()


# --- replay ----------------------------------------------------------------------------


def test_replaying_an_already_saved_run_writes_nothing_and_returns_its_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    recorded = _run(tmp_path, mode=CacheMode.RECORD)
    out = save_run(recorded, root)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    index_before = _read(root / "index.json")

    replayed = _run(tmp_path, mode=CacheMode.REPLAY, sdk=ForbiddenSDK())
    assert save_run(replayed, root, replayed=True) == out

    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert _read(root / "index.json") == index_before
    assert json.loads(before["run.json"])["cost_usd"] > 0  # the recording's own cost is kept


def test_replaying_a_recording_that_was_never_saved_writes_it_marked_as_a_replay(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    _run(tmp_path, mode=CacheMode.RECORD)  # recorded, but never saved (made before S5.2)
    replayed = _run(tmp_path, mode=CacheMode.REPLAY, sdk=ForbiddenSDK())
    out = save_run(replayed, root, replayed=True)

    assert {p.name for p in out.iterdir()} == FILES
    meta = json.loads(_read(out / "run.json"))
    assert meta["replayed"] is True and meta["cost_usd"] == 0.0
    assert meta["cached_calls"] == meta["llm_calls"] == 11
    entry = json.loads(_read(root / "index.json"))["runs"][0]
    assert entry["replayed"] is True and entry["cost_usd"] == 0.0
    assert AgentState.from_json(_read(out / "state.json")) == replayed.state


# --- the number check is saved (S5.3) ---------------------------------------------------


def test_the_number_check_is_saved_in_run_json_and_decision_md(tmp_path: Path) -> None:
    result = _run(tmp_path, sdk=PipelineSDK(thesis_extra=" A level of Rs 9,999."))
    out = save_run(result, tmp_path / "results")
    meta = json.loads(_read(out / "run.json"))
    assert meta["claims"]["checked"] == 1
    assert [u["text"] for u in meta["claims"]["unmatched"]] == ["Rs 9,999"]
    assert meta["claims"]["unmatched"][0]["where"] == "decision.investment_thesis"
    decision = _read(out / "decision.md")
    assert "## Number check" in decision and "1 of 1 numbers match no source" in decision
    assert {p.name for p in out.iterdir()} == FILES  # no new file: the set is unchanged
