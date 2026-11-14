"""S5.1: `ta-in run` runs a fixture cell, rejects bad flags clearly, and replays with no network."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pipeline_sdk import ForbiddenSDK, PipelineSDK

from ta_in.cli import main
from ta_in.fixtures import Manifest

FIXTURES = Path(__file__).parent.parent / "fixtures"
FAKE_KEY = "sk-ant-FAKE-0123456789-secret"


def _cell() -> tuple[str, str]:
    cell = Manifest.model_validate_json((FIXTURES / "manifest.json").read_text("utf-8")).cells[0]
    return cell.ticker, cell.as_of.date().isoformat()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run in an empty directory (so no repo `.env` is read) with a fake key and temp dirs."""
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("TA_") or name in {"ANTHROPIC_API_KEY", "CURRENTS_API_KEY"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TA_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("TA_RESULTS_DIR", str(tmp_path / "results"))


def _args(*extra: str, ticker: str | None = None, date: str | None = None) -> list[str]:
    t, d = _cell()
    return [
        "run",
        "--ticker",
        ticker or t,
        "--date",
        date or d,
        "--fixtures",
        str(FIXTURES),
        *extra,
    ]


def _run(
    capsys: pytest.CaptureFixture[str], argv: list[str], sdk: Any = None
) -> tuple[int, str, str]:
    code = main(argv, sdk=sdk)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- criterion 1: runs a fixture cell from the shell -----------------------------------


def test_a_fixture_cell_runs_and_prints_the_decision_and_the_run_report(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    sdk = PipelineSDK()
    code, out, err = _run(capsys, _args("--run-id", "run-1"), sdk)

    assert code == 0 and err == ""
    ticker, date = _cell()
    assert f"{ticker}  {date}" in out
    assert "Rating: Overweight" in out
    assert "Executive summary: Enter in two tranches" in out
    assert "Investment thesis:" in out and "Aggressive" in out
    assert "run id:" in out and "run-1" in out
    assert "calls:" in out and "11" in out and "0 cached" in out
    assert "tokens:" in out and "cost:" in out and "time:" in out and "trace:" in out
    assert len(sdk.calls) == 11
    trace = tmp_path / "traces" / "run-1.jsonl"
    assert trace.exists()
    assert len(trace.read_text("utf-8").splitlines()) == 12  # run_start + 11 calls


def test_the_shell_entry_point_shows_every_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ta_in.cli", "run", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    for flag in ("--ticker", "--date", "--analysts", "--rounds", "--risk-rounds", "--replay"):
        assert flag in result.stdout
    assert "Traceback" not in result.stderr


def test_only_the_selected_analysts_run(capsys: pytest.CaptureFixture[str]) -> None:
    sdk = PipelineSDK()
    code, out, _ = _run(capsys, _args("--analysts", " News , market "), sdk)
    assert code == 0 and "fundamentals" not in sdk.roles()
    assert sdk.roles().count("news") == 1


@pytest.mark.parametrize(
    ("rounds", "risk", "calls"), [("0", "0", 6), ("2", "1", 13), ("1", "0", 8)]
)
def test_rounds_flags_set_the_debate_and_risk_rounds(
    capsys: pytest.CaptureFixture[str], rounds: str, risk: str, calls: int
) -> None:
    sdk = PipelineSDK()
    code, _, _ = _run(capsys, _args("--rounds", rounds, "--risk-rounds", risk), sdk)
    assert code == 0 and len(sdk.calls) == calls


def test_settings_rounds_are_used_when_no_flag_is_given(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TA_DEBATE_ROUNDS", "0")
    monkeypatch.setenv("TA_RISK_ROUNDS", "0")
    sdk = PipelineSDK()
    assert _run(capsys, _args(), sdk)[0] == 0
    assert len(sdk.calls) == 6


def test_the_key_never_appears_in_the_output(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, err = _run(capsys, _args(), PipelineSDK())
    assert FAKE_KEY not in out + err


# --- criterion 2: missing or invalid flags give a clear error --------------------------


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["run", "--date", "2026-10-21"], "--ticker"),
        (["run", "--ticker", "RELIANCE.NS"], "--date"),
        (["run", "--ticker", "RELIANCE.NS", "--date", "2026-13-40"], "YYYY-MM-DD"),
        (["run", "--ticker", "RELIANCE.NS", "--date", "tomorrow"], "YYYY-MM-DD"),
        (["run", "--ticker", "RELIANCE.NS", "--date", "2026-10-21T16:00"], "offset"),
        (["frobnicate"], "invalid choice"),
        ([], "command"),
    ],
)
def test_missing_or_malformed_flags_say_what_is_wrong(
    capsys: pytest.CaptureFixture[str], argv: list[str], message: str
) -> None:
    sdk = PipelineSDK()
    code, out, err = _run(capsys, argv, sdk)
    assert code == 2 and out == ""
    assert message in err and "Traceback" not in err
    assert sdk.calls == []


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--analysts", "market,oracle"], "oracle"),
        (["--analysts", ""], "no analysts"),
        (["--rounds", "-1"], "--rounds"),
        (["--rounds", "4"], "--rounds"),
        (["--rounds", "many"], "--rounds"),
        (["--risk-rounds", "9"], "--risk-rounds"),
    ],
)
def test_invalid_option_values_say_what_is_wrong(
    capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    sdk = PipelineSDK()
    code, out, err = _run(capsys, _args(*extra), sdk)
    assert code == 2 and out == ""
    assert message in err and "Traceback" not in err
    assert sdk.calls == []


def test_a_ticker_or_date_that_is_not_frozen_lists_what_is(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ticker, date = _cell()
    for bad in (_args(ticker="NOSUCH.NS"), _args(date="2020-01-01")):
        sdk = PipelineSDK()
        code, out, err = _run(capsys, bad, sdk)
        assert code == 2 and out == ""
        assert "no frozen cell" in err and f"{ticker} {date}" in err
        assert "Traceback" not in err and sdk.calls == []


def test_a_missing_fixtures_directory_is_explained(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    argv = _args()
    argv[argv.index("--fixtures") + 1] = str(tmp_path / "nowhere")
    code, _, err = _run(capsys, argv, PipelineSDK())
    assert code == 2 and "freeze-fixtures" in err and "Traceback" not in err


def test_a_missing_api_key_is_a_clear_settings_error_when_not_replaying(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    code, _, err = _run(capsys, _args(), PipelineSDK())
    assert code == 2 and "ANTHROPIC_API_KEY" in err and "Traceback" not in err


def test_a_failed_run_exits_one_with_a_message_and_no_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sdk = PipelineSDK(failing=("market", "news"))
    code, out, err = _run(capsys, _args("--analysts", "market,news"), sdk)
    assert code == 1 and out == ""
    assert "every selected analyst failed" in err and "Traceback" not in err


# --- criterion 3: --replay makes no network calls --------------------------------------


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the network was used during --replay")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr("anthropic.Anthropic", refuse)


def test_replay_reproduces_the_recorded_run_with_no_network_and_no_key(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TA_CACHE_MODE", "record")
    recorded_code, recorded_out, _ = _run(capsys, _args("--run-id", "run-1"), PipelineSDK())
    assert recorded_code == 0

    monkeypatch.delenv("TA_CACHE_MODE")
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # replay needs no key
    _block_network(monkeypatch)
    forbidden = ForbiddenSDK()
    code, out, err = _run(capsys, _args("--replay", "--run-id", "run-1"), forbidden)

    assert code == 0 and err == "" and forbidden.calls == []
    assert "11 (11 cached)" in out and "$0.0000" in out
    decision = lambda text: text.split("Run report")[0]  # noqa: E731
    assert decision(out) == decision(recorded_out)
    # A replay writes no trace of its own; the recording's trace is left untouched.
    assert [p.name for p in (tmp_path / "traces").iterdir()] == ["run-1.jsonl"]
    assert "none: a replay writes no trace" in out


def test_a_used_run_id_is_refused_instead_of_overwriting_its_trace(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run(capsys, _args("--run-id", "run-1"), PipelineSDK())[0] == 0
    sdk = PipelineSDK()
    code, out, err = _run(capsys, _args("--run-id", "run-1"), sdk)
    assert code == 2 and out == "" and "already exists" in err and "Traceback" not in err
    assert sdk.calls == []


def test_replay_with_the_real_client_path_never_builds_an_sdk(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TA_CACHE_MODE", "record")
    assert _run(capsys, _args(), PipelineSDK())[0] == 0
    monkeypatch.delenv("TA_CACHE_MODE")
    _block_network(monkeypatch)
    code, out, err = _run(capsys, _args("--replay"))  # no sdk seam: the real LLMClient path
    assert code == 0 and "11 (11 cached)" in out
    assert err == ""


def test_replaying_a_run_that_was_never_recorded_exits_one_with_a_hint(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _block_network(monkeypatch)
    code, out, err = _run(capsys, _args("--replay"))
    assert code == 1 and out == ""
    assert "recorded" in err and "Traceback" not in err


def test_the_trace_summary_matches_the_report(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _run(capsys, _args("--run-id", "run-2"), PipelineSDK())
    lines = [
        json.loads(x) for x in (tmp_path / "traces" / "run-2.jsonl").read_text("utf-8").splitlines()
    ]
    assert lines[0]["run_id"] == "run-2"
    assert sum(1 for e in lines if e["type"] == "call") == 11


# --- API errors are one clean line, not a traceback (found by the first real run) ------


def _status_error(status: int, message: str) -> Exception:
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    cls = anthropic.BadRequestError if status == 400 else anthropic.InternalServerError
    return cls(message, response=response, body=None)


class _FailingSDK(PipelineSDK):
    """Works until the research manager, whose call fails with `error` (as the real one did)."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def _create(self, **kw: Any) -> Any:
        if self.role_of(kw["system"]) == "manager":
            self.calls.append(kw)
            raise self.error
        return super()._create(**kw)


def test_a_rejected_request_is_a_clean_one_line_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sdk = _FailingSDK(_status_error(400, "tool_choice: type tool is not supported for this model"))
    code, out, err = _run(capsys, _args(), sdk)
    assert code == 1 and out == ""
    assert "the model API failed" in err and "BadRequestError" in err
    assert "tool_choice" in err and "Traceback" not in err and FAKE_KEY not in err


def test_a_run_saves_its_reports_and_prints_where(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    ticker, day = _cell()
    code, out, err = _run(capsys, _args("--run-id", "save-1"), PipelineSDK())
    assert code == 0 and err == ""
    folder = tmp_path / "results" / ticker / day / "save-1"
    assert "results:" in out and str(folder) in out
    for name in ("market_report.md", "decision.json", "state.json", "run.json", "decision.md"):
        assert (folder / name).exists(), name
    index = json.loads((tmp_path / "results" / "index.json").read_text("utf-8"))
    assert [e["run_id"] for e in index["runs"]] == ["save-1"]
    assert index["runs"][0]["cost_usd"] > 0


def test_results_dir_flag_overrides_the_setting(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    code, out, _ = _run(
        capsys, _args("--run-id", "r1", "--results-dir", str(elsewhere)), PipelineSDK()
    )
    assert code == 0 and (elsewhere / "index.json").exists()
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("bad", ["../x", "a/b", "a b", "", ".hidden", "x" * 81])
def test_an_unsafe_run_id_is_refused_before_anything_runs_or_is_written(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, bad: str
) -> None:
    sdk = PipelineSDK()
    code, out, err = _run(capsys, _args("--run-id", bad), sdk)
    assert code == 2 and out == "" and "--run-id" in err and "Traceback" not in err
    assert sdk.calls == []
    assert not (tmp_path / "traces").exists() and not (tmp_path / "results").exists()
    assert not (tmp_path.parent / "x.jsonl").exists()


def test_a_failed_save_exits_one_but_the_decision_is_still_printed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    code, out, err = _run(capsys, _args("--results-dir", str(blocker)), PipelineSDK())
    assert code == 1
    assert "Rating:" in out and "Run report" in out  # the result is never hidden by a save failure
    assert "could not save" in err and "Traceback" not in err


def test_replaying_a_saved_run_leaves_its_files_alone(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ticker, day = _cell()
    monkeypatch.setenv("TA_CACHE_MODE", "record")
    assert _run(capsys, _args("--run-id", "keep-1"), PipelineSDK())[0] == 0
    folder = tmp_path / "results" / ticker / day / "keep-1"
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    monkeypatch.delenv("TA_CACHE_MODE")
    code, out, _ = _run(capsys, _args("--replay", "--run-id", "keep-1"))
    assert code == 0 and "already saved" in out
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_replaying_a_run_that_was_never_saved_saves_it_marked_as_a_replay(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ticker, day = _cell()
    monkeypatch.setenv("TA_CACHE_MODE", "record")
    assert _run(capsys, _args("--run-id", "old-1"), PipelineSDK())[0] == 0
    # Simulate a recording made before results were saved.
    import shutil

    shutil.rmtree(tmp_path / "results")
    monkeypatch.delenv("TA_CACHE_MODE")
    code, out, _ = _run(capsys, _args("--replay", "--run-id", "old-1"))
    assert code == 0
    meta = json.loads(
        (tmp_path / "results" / ticker / day / "old-1" / "run.json").read_text("utf-8")
    )
    assert meta["replayed"] is True and meta["cost_usd"] == 0.0


def test_a_connection_failure_is_a_clean_one_line_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    sdk = _FailingSDK(anthropic.APIConnectionError(request=request))
    code, out, err = _run(capsys, _args(), sdk)
    assert code == 1 and out == ""
    assert "the model API failed" in err and "APIConnectionError" in err
    assert "Traceback" not in err


# --- the number check is printed and saved (S5.3) --------------------------------------


def test_the_number_check_lists_unmatched_figures_and_does_not_change_the_exit_code(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    sdk = PipelineSDK(thesis_extra=" A target of Rs 9,999.")
    code, out, err = _run(capsys, _args("--run-id", "num-1"), sdk)
    assert code == 0 and err == ""
    assert "Number check: 1 of 1 numbers match no source" in out
    assert "decision.investment_thesis: Rs 9,999" in out
    assert out.index("Number check") < out.index("Run report")
    ticker, day = _cell()
    folder = tmp_path / "results" / ticker / day / "num-1"
    meta = json.loads((folder / "run.json").read_text("utf-8"))
    assert meta["claims"]["checked"] == 1 and meta["claims"]["unmatched"][0]["text"] == "Rs 9,999"
    decision_md = (folder / "decision.md").read_text("utf-8")
    assert "## Number check" in decision_md and "Rs 9,999" in decision_md


def test_a_run_with_no_numbers_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = _run(capsys, _args(), PipelineSDK())
    assert "Number check: no numbers to check" in out


def test_a_replay_prints_the_same_number_check_as_the_recording(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TA_CACHE_MODE", "record")
    sdk = PipelineSDK(thesis_extra=" A target of Rs 9,999.")
    _, recorded, _ = _run(capsys, _args("--run-id", "num-2"), sdk)
    monkeypatch.delenv("TA_CACHE_MODE")
    code, replayed, _ = _run(capsys, _args("--replay", "--run-id", "num-2"))
    assert code == 0

    def block(text: str) -> str:
        return text.split("Number check")[1].split("Run report")[0]

    assert block(replayed) == block(recorded)
