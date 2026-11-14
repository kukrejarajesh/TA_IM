"""`ta-in run`: run the whole pipeline for one frozen fixture cell and print the decision.

    ta-in run --ticker RELIANCE.NS --date 2026-10-21 [--analysts market,news] [--rounds 1]
              [--risk-rounds 1] [--replay] [--run-id ID] [--results-dir DIR] [--fixtures DIR]

Data comes only from the frozen fixtures (`freeze-fixtures` writes them); a ticker or date that was
never frozen is an error that lists what exists. `--replay` answers every model call from the
recorded response cache: it needs no Anthropic key, builds no SDK and so makes no network call.
Record a run first with `TA_CACHE_MODE=record`.

Exit codes: 0 success, 1 the run failed, 2 bad flags, settings or fixtures. Errors go to stderr as
one clear line, never a traceback. The report on stdout ends with the run's cost and time.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

DEFAULT_FIXTURES = Path("tests") / "fixtures"
MAX_ROUNDS = 3
_REPLAY_PLACEHOLDER_KEY = "replay-does-not-use-this"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="ta-in", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)
    run = sub.add_parser(
        "run",
        prog="ta-in run",
        help="run the pipeline for one frozen fixture cell",
        description="Run the pipeline for one frozen fixture cell and print the decision.",
    )
    run.add_argument("--ticker", required=True, help="NSE ticker, e.g. RELIANCE.NS")
    run.add_argument(
        "--date", required=True, help="YYYY-MM-DD (16:00 IST) or an ISO datetime with an offset"
    )
    run.add_argument(
        "--analysts",
        default=None,
        help="comma-separated: market,news,fundamentals (default: all three)",
    )
    run.add_argument("--rounds", type=int, default=None, help="bull/bear debate rounds, 0 to 3")
    run.add_argument("--risk-rounds", type=int, default=None, help="risk debate rounds, 0 to 3")
    run.add_argument(
        "--replay",
        action="store_true",
        help="answer from the recorded response cache only (no network, no API key)",
    )
    run.add_argument("--run-id", default=None, help="run id (use the recorded id when replaying)")
    run.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="where to save the reports and the run index (default: Settings.results_dir)",
    )
    run.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES,
        help=f"frozen snapshot directory (default: {DEFAULT_FIXTURES})",
    )
    return parser


def _parse_date(text: str) -> datetime:
    from ta_in.data import IST

    try:
        if "T" in text or " " in text.strip():
            parsed = datetime.fromisoformat(text.strip())
            if parsed.tzinfo is None:
                raise ValueError(
                    "a datetime needs a UTC offset, for example 2026-10-21T16:00+05:30"
                )
            return parsed
        return datetime.combine(date.fromisoformat(text.strip()), time(16, 0), tzinfo=IST)
    except ValueError as err:
        hint = str(err) if "offset" in str(err) else "expected YYYY-MM-DD, for example 2026-10-21"
        raise ValueError(f"invalid --date {text!r}: {hint}") from None


def _check_rounds(flag: str, value: int | None) -> None:
    if value is not None and not 0 <= value <= MAX_ROUNDS:
        raise ValueError(f"{flag} must be between 0 and {MAX_ROUNDS}, got {value}")


def _frozen_cell_as_of(fixtures: Path, ticker: str, as_of: datetime) -> datetime:
    """The frozen cell's as_of for this ticker and day, or a `ValueError` listing what exists."""
    from ta_in.data import IST
    from ta_in.fixtures import Manifest

    manifest_path = fixtures / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(
            f"no fixtures found in {fixtures} (run freeze-fixtures, or pass --fixtures DIR)"
        )
    cells = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8")).cells
    wanted = ticker.strip().upper()
    for cell in cells:
        if (
            cell.ticker == wanted
            and cell.as_of.astimezone(IST).date() == as_of.astimezone(IST).date()
        ):
            return cell.as_of
    frozen = ", ".join(f"{c.ticker} {c.as_of.astimezone(IST).date()}" for c in cells)
    raise ValueError(
        f"no frozen cell for {wanted} on {as_of.astimezone(IST).date()}; frozen cells: {frozen}"
    )


def _report(result: Any, ticker: str, as_of: datetime) -> str:
    from ta_in.data import IST

    d = result.decision
    lines = [
        f"{ticker}  {as_of.astimezone(IST).date().isoformat()}",
        f"Rating: {d.rating.value}",
        f"Executive summary: {d.executive_summary}",
        f"Investment thesis: {d.investment_thesis}",
    ]
    if d.price_target is not None:
        lines.append(f"Price target: {d.price_target}")
    if d.time_horizon:
        lines.append(f"Time horizon: {d.time_horizon}")
    lines += [
        "",
        "Run report",
        f"  run id:  {result.run_id}",
        f"  calls:   {result.llm_calls} ({result.cached_calls} cached)",
        f"  tokens:  in {result.input_tokens}, out {result.output_tokens}",
        f"  cost:    ${result.cost_usd:.4f}",
        f"  time:    {result.elapsed_s:.1f} s",
        f"  trace:   {result.trace_path or '(none: a replay writes no trace)'}",
    ]
    return "\n".join(lines)


def _run(args: argparse.Namespace, sdk: Any) -> int:
    # Heavy imports stay here so `--help` and the flag errors are fast and light.
    import anthropic
    from pydantic import ValidationError

    from ta_in.agents import ANALYST_NAMES, DebateError, parse_analysts
    from ta_in.config.settings import SettingsError, load_settings
    from ta_in.data.point_in_time import PointInTimeError
    from ta_in.fixtures import FixtureMissingError, FrozenDataProvider
    from ta_in.llm import (
        BudgetExceeded,
        CacheMiss,
        CacheMode,
        LLMClient,
        ResponseCache,
        StructuredOutputError,
        TraceWriter,
        new_run_id,
    )
    from ta_in.propagate import AllAnalystsFailedError, propagate
    from ta_in.reports import run_dir, save_run, validate_run_id

    try:
        as_of = _parse_date(args.date)
        _check_rounds("--rounds", args.rounds)
        _check_rounds("--risk-rounds", args.risk_rounds)
        if args.run_id is not None:  # the id becomes a trace file name and a results folder name
            try:
                validate_run_id(args.run_id)
            except ValueError as err:
                raise ValueError(f"--run-id: {err}") from None
        analysts = (
            parse_analysts(args.analysts, ANALYST_NAMES) if args.analysts is not None else None
        )
        as_of = _frozen_cell_as_of(args.fixtures, args.ticker, as_of)
        if args.replay:  # replay never calls the model, so no Anthropic key is needed
            os.environ.setdefault("ANTHROPIC_API_KEY", _REPLAY_PLACEHOLDER_KEY)
            if not os.environ["ANTHROPIC_API_KEY"].strip():
                os.environ["ANTHROPIC_API_KEY"] = _REPLAY_PLACEHOLDER_KEY
        settings = load_settings()
    except (ValueError, SettingsError) as err:
        print(f"ta-in: error: {err}", file=sys.stderr)
        return 2

    updates: dict[str, Any] = {}
    if args.rounds is not None:
        updates["debate_rounds"] = args.rounds
    if args.risk_rounds is not None:
        updates["risk_rounds"] = args.risk_rounds
    if updates:
        settings = settings.model_copy(update=updates)

    run_id = args.run_id if args.run_id is not None else new_run_id()
    mode = CacheMode.REPLAY if args.replay else CacheMode(settings.cache_mode)
    trace = None  # a replay writes no trace: the recording's own trace already exists
    if not args.replay:
        try:
            trace = TraceWriter(settings.trace_dir, run_id=run_id)
        except FileExistsError:
            print(
                f"ta-in: error: a trace for run id {run_id!r} already exists in "
                f"{settings.trace_dir}; pick another --run-id (or use --replay to replay it)",
                file=sys.stderr,
            )
            return 2
    client = LLMClient(
        settings, sdk=sdk, cache=ResponseCache(settings.cache_dir), cache_mode=mode, trace=trace
    )
    kwargs: dict[str, Any] = {"analysts": analysts} if analysts is not None else {}
    try:
        result = propagate(
            args.ticker,
            as_of,
            settings=settings,
            client=client,
            provider=FrozenDataProvider(args.fixtures),
            trace=trace,
            run_id=run_id,
            **kwargs,
        )
    except CacheMiss as err:
        print(
            f"ta-in: error: this run was never recorded ({err}). Record it first with "
            "TA_CACHE_MODE=record, then replay it with the same flags.",
            file=sys.stderr,
        )
        return 1
    except anthropic.APIError as err:  # rejected request, connection failure, timeout, rate limit
        print(f"ta-in: error: the model API failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    except (
        AllAnalystsFailedError,
        StructuredOutputError,
        DebateError,
        BudgetExceeded,
        PointInTimeError,
        FixtureMissingError,
        ValidationError,
    ) as err:
        print(f"ta-in: error: the run failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1

    print(_report(result, result.state.ticker, as_of))  # the result is shown before it is saved
    root = args.results_dir if args.results_dir is not None else settings.results_dir
    try:
        existed = run_dir(root, result.state.ticker, result.state.as_of, result.run_id).exists()
        saved = save_run(result, root, replayed=args.replay)
    except (OSError, ValueError) as err:
        print(f"ta-in: error: could not save the results: {err}", file=sys.stderr)
        return 1
    note = " (already saved)" if existed and args.replay else ""
    print(f"  results: {saved}{note}")
    return 0


def main(argv: list[str] | None = None, *, sdk: Any = None) -> int:
    """Entry point for `ta-in`. `sdk` is a test seam for `LLMClient`; real runs leave it None."""
    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 2
    return _run(args, sdk)


if __name__ == "__main__":
    raise SystemExit(main())
