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
