"""`ta-in run`: run the whole pipeline for one stock and print the decision.

    ta-in run --ticker RELIANCE.NS --date 2026-10-21 [--analysts market,news] [--rounds 1]
              [--risk-rounds 1] [--replay] [--run-id ID] [--results-dir DIR] [--fixtures DIR]
    ta-in run --live --ticker INFY.NS [--date YYYY-MM-DD] [same options, but no --fixtures]

By default the data comes from the frozen fixtures (`freeze-fixtures` writes them); a ticker or date
that was never frozen is an error that lists what exists. `--live` reads today's data from Kite,
Currents and bfinance instead, works for any NSE ticker, saves the data the run used next to its
results, and records the model answers so the run can be replayed. `--live` and `--fixtures` cannot
be combined. `--replay` answers every model call from the recorded response cache: it needs no
Anthropic key, builds no SDK and so makes no network call; `--live --replay --run-id ID` replays a
saved live run from its own snapshot (no credentials, no network).

Exit codes: 0 success, 1 the run failed, 2 bad flags, settings, credentials or fixtures. Errors go
to stderr as one clear line, never a traceback. The report on stdout ends with the run's cost and
time.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

DEFAULT_FIXTURES = Path("tests") / "fixtures"
MAX_ROUNDS = 3
FUNDAMENTALS_WINDOW = timedelta(days=2)  # statements are current-date only
NEWS_WINDOW = timedelta(days=30)  # Currents reaches back this far
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
        help="run the pipeline for one stock (frozen fixtures, or --live)",
        description="Run the pipeline for one stock and print the decision.",
    )
    run.add_argument("--ticker", required=True, help="NSE ticker, e.g. RELIANCE.NS")
    run.add_argument(
        "--date",
        default=None,
        help="YYYY-MM-DD (16:00 IST) or an ISO datetime with an offset; "
        "required unless --live (then the default is now)",
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
    source = run.add_mutually_exclusive_group()
    source.add_argument(
        "--live",
        action="store_true",
        help="use live data (Kite, Currents, bfinance) for any NSE ticker; needs credentials",
    )
    source.add_argument(
        "--fixtures",
        type=Path,
        default=None,
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


def _live_as_of(text: str | None, now: datetime) -> datetime:
    """The moment a live run is as of: now by default, a past day's close, never the future."""
    from ta_in.data import IST

    if text is None:
        return now
    parsed = _parse_date(text)
    if "T" in text or " " in text.strip():
        if parsed > now:
            raise ValueError(f"--date {text!r} is in the future (it is {now.isoformat()})")
        return parsed
    day = parsed.astimezone(IST).date()
    today = now.astimezone(IST).date()
    if day > today:
        raise ValueError(f"--date {text!r} is in the future (today is {today.isoformat()})")
    return now if day == today else parsed


def _degradation_notes(as_of: datetime, now: datetime) -> list[str]:
    """What an older as_of costs a live run: sources that only serve recent data."""
    age = now - as_of
    notes: list[str] = []
    if age > FUNDAMENTALS_WINDOW:
        notes.append("fundamentals will be withheld (statements are current-date only)")
    if age > NEWS_WINDOW:
        notes.append("news is unavailable (Currents reaches back 30 days)")
    return notes


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


def _find_live_snapshot(root: Path, ticker: str, run_id: str, date_text: str | None) -> Path:
    """The saved `data/` folder of a live run, found by ticker and run id (and day, if given)."""
    from ta_in.data import IST

    wanted_day = _parse_date(date_text).astimezone(IST).date().isoformat() if date_text else None
    base = root / ticker
    found = [
        day / run_id / "data"
        for day in sorted(base.glob("*"))
        if (day / run_id / "data").is_dir() and (wanted_day is None or day.name == wanted_day)
    ]
    if not found:
        raise ValueError(
            f"no saved live run {run_id!r} for {ticker} in {root} "
            "(a live run saves its data next to its results; use the same --results-dir)"
        )
    return found[0]


def _short(text: str, limit: int = 90) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 3] + "..."


def _live_data_block(
    recorder: Any,
    ticker: str,
    as_of: datetime,
    degraded: tuple[str, ...],
    notes: list[str],
    cache_note: str | None,
) -> str:
    """Which sources were live and what each returned (S8.5 adds freshness warnings)."""
    from ta_in.data import IST
    from ta_in.fundamentals import Withheld

    rec = recorder.recorded(ticker)
    bars = rec.prices or []
    lines = [f"Data: LIVE, as of {as_of.astimezone(IST):%Y-%m-%d %H:%M} IST"]
    name = rec.identity.name if rec.identity is not None else "?"
    lines.append(f"  identity: Kite ({name})")
    lines.append(f"  prices: Kite ({len(bars)} bars, last bar {bars[-1].date.isoformat()})")
    news_degraded = next((d for d in degraded if d.startswith("news:")), None)
    failed = [e for e in rec.events if e.get("event") == "news.provider_failed"]
    if news_degraded is not None:
        lines.append(f"  news: degraded ({news_degraded.removeprefix('news:').strip()})")
    elif failed:
        lines.append(f"  news: Currents failed ({_short(str(failed[0].get('error', '')))})")
    elif rec.news is None:
        lines.append("  news: not requested")
    else:
        lines.append(f"  news: Currents ({len(rec.news)} items)")
    funds = rec.fundamentals
    if funds is None:
        lines.append("  fundamentals: not requested")
    elif isinstance(funds, Withheld):
        lines.append(f"  fundamentals: withheld ({_short(funds.reason)})")
    else:
        newest = max(funds.income_statement, default="no income statement")
        lines.append(f"  fundamentals: bfinance (statements to {newest})")
    lines += [f"  note: {n}" for n in notes]
    if cache_note:
        lines.append(f"  models: {cache_note}")
    return "\n".join(lines)


def _report(result: Any, ticker: str, as_of: datetime, data_block: str | None = None) -> str:
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
    lines += ["", result.claims.render(), ""]
    if data_block:
        lines += [data_block, ""]
    lines += [
        "Run report",
        f"  run id:  {result.run_id}",
        f"  calls:   {result.llm_calls} ({result.cached_calls} cached)",
        f"  tokens:  in {result.input_tokens}, out {result.output_tokens}",
        f"  cost:    ${result.cost_usd:.4f}",
        f"  time:    {result.elapsed_s:.1f} s",
        f"  trace:   {result.trace_path or '(none: a replay writes no trace)'}",
    ]
    return "\n".join(lines)


def _run(
    args: argparse.Namespace,
    sdk: Any,
    clock: Callable[[], datetime] | None,
    live_factory: Callable[..., Any] | None,
) -> int:
    # Heavy imports stay here so `--help` and the flag errors are fast and light.
    import anthropic
    from pydantic import SecretStr, ValidationError

    from ta_in import live
    from ta_in.agents import ANALYST_NAMES, DebateError, parse_analysts
    from ta_in.config.settings import Settings, SettingsError, load_settings
    from ta_in.data import IST, UnknownTickerError
    from ta_in.data.point_in_time import PointInTimeError
    from ta_in.fixtures import FixtureMissingError, FrozenDataProvider
    from ta_in.live_snapshot import RecordingProvider, write_snapshot
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
    from ta_in.reports import run_dir, save_run, validate_run_id, validate_ticker

    now_fn: Callable[[], datetime] = clock or (lambda: datetime.now(IST).replace(microsecond=0))
    is_live = bool(args.live)
    live_run = is_live and not args.replay
    live_replay = is_live and args.replay
    ticker = str(args.ticker).strip().upper()
    notes: list[str] = []
    recording_note: str | None = None
    snapshot_dir: Path | None = None
    provider_root: Path | None = None
    analysts: list[str] | None = None

    try:
        _check_rounds("--rounds", args.rounds)
        _check_rounds("--risk-rounds", args.risk_rounds)
        if args.run_id is not None:  # the id becomes a trace file name and a results folder name
            try:
                validate_run_id(args.run_id)
            except ValueError as err:
                raise ValueError(f"--run-id: {err}") from None
        if args.analysts is not None:
            analysts = parse_analysts(args.analysts, ANALYST_NAMES)
        if args.date is None and not is_live:
            raise ValueError("--date is required unless --live is given")
        if is_live and not ticker.endswith(".NS"):
            raise ValueError(f"live mode supports NSE tickers ending in .NS, got {ticker!r}")
        if live_replay and args.run_id is None:
            raise ValueError("--live --replay needs --run-id (the live run to replay)")
        if args.replay:  # replay never calls the model, so no Anthropic key is needed
            os.environ.setdefault("ANTHROPIC_API_KEY", _REPLAY_PLACEHOLDER_KEY)
            if not os.environ["ANTHROPIC_API_KEY"].strip():
                os.environ["ANTHROPIC_API_KEY"] = _REPLAY_PLACEHOLDER_KEY

        missing: list[str] = []
        try:
            settings = load_settings()
        except SettingsError as err:
            if not (live_run and "ANTHROPIC_API_KEY" in str(err)):
                raise
            missing.append("ANTHROPIC_API_KEY")  # keep going to name the Kite ones as well
            settings = Settings(ANTHROPIC_API_KEY=SecretStr(_REPLAY_PLACEHOLDER_KEY))
        sources: Any = None
        if live_run:
            if CacheMode(settings.cache_mode) is CacheMode.REPLAY:
                raise ValueError(
                    "TA_CACHE_MODE=replay cannot be used for a live run, which calls the model; "
                    "use --live --replay --run-id ID to replay a saved live run"
                )
            factory = live_factory or live.make_live_sources
            try:
                sources = factory(settings, clock=now_fn)  # raises for missing Kite credentials
            except live.LiveConfigError as err:
                raise live.LiveConfigError([*missing, *err.missing]) from None
            if missing:  # only the Anthropic key was missing
                raise live.LiveConfigError(missing)

        results_root = args.results_dir if args.results_dir is not None else settings.results_dir
        if live_replay:
            validate_ticker(ticker)
            snapshot_dir = _find_live_snapshot(results_root, ticker, args.run_id, args.date)
            provider_root = snapshot_dir
            day = _parse_date(snapshot_dir.parent.parent.name)
            as_of = _frozen_cell_as_of(snapshot_dir, ticker, day)
        elif live_run:
            as_of = _live_as_of(args.date, now_fn())
            notes = _degradation_notes(as_of, now_fn())
        else:
            provider_root = args.fixtures if args.fixtures is not None else DEFAULT_FIXTURES
            as_of = _frozen_cell_as_of(provider_root, ticker, _parse_date(args.date))
    except live.LiveConfigError as err:
        print(f"ta-in: error: {err}", file=sys.stderr)
        return 2
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
    if live_run and mode is CacheMode.OFF:
        mode = CacheMode.RECORD  # a live run is only replayable if its answers were recorded
        recording_note = f"recording answers to {settings.cache_dir} so this run can be replayed"
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

    recorder: Any = None
    degraded: tuple[str, ...] = ()
    provider: Any
    if live_run:
        degraded = tuple(sources.degraded)
        recorder = RecordingProvider(lambda sink: live.LiveDataProvider(sources, event_sink=sink))
        provider = recorder
    else:
        assert provider_root is not None
        provider = FrozenDataProvider(provider_root)

    kwargs: dict[str, Any] = {"analysts": analysts} if analysts is not None else {}
    try:
        result = propagate(
            ticker,
            as_of,
            settings=settings,
            client=client,
            provider=provider,
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
    except UnknownTickerError as err:
        print(f"ta-in: error: unknown ticker: {err}", file=sys.stderr)
        return 2
    except live.LiveSourceError as err:
        print(f"ta-in: error: live data failed: {err}", file=sys.stderr)
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

    data_block: str | None = None
    if live_run:
        data_block = _live_data_block(
            recorder, result.state.ticker, as_of, degraded, notes, recording_note
        )
    elif live_replay and snapshot_dir is not None:
        data_block = f"Data: LIVE (replayed from the saved snapshot)\n  snapshot: {snapshot_dir}"
    print(_report(result, result.state.ticker, as_of, data_block))  # shown before it is saved
    try:
        existed = run_dir(
            results_root, result.state.ticker, result.state.as_of, result.run_id
        ).exists()
        saved = save_run(result, results_root, replayed=args.replay)
    except (OSError, ValueError) as err:
        print(f"ta-in: error: could not save the results: {err}", file=sys.stderr)
        return 1
    note = " (already saved)" if existed and args.replay else ""
    print(f"  results: {saved}{note}")

    if live_run:
        news_source = "degraded" if any(d.startswith("news:") for d in degraded) else "currents"
        try:
            data_path = write_snapshot(
                recorder,
                saved,
                ticker=result.state.ticker,
                frozen_at=now_fn(),
                sources={
                    "mode": "live",
                    "prices": "kite",
                    "identity": "kite",
                    "news": news_source,
                    "fundamentals": "bfinance",
                },
            )
        except (OSError, ValueError) as err:
            print(f"ta-in: error: could not save the data snapshot: {err}", file=sys.stderr)
            return 1
        print(f"  data:    {data_path}")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    sdk: Any = None,
    clock: Callable[[], datetime] | None = None,
    live_factory: Callable[..., Any] | None = None,
) -> int:
    """Entry point for `ta-in`. `sdk`, `clock` and `live_factory` are test seams; real runs leave
    them None."""
    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 2
    return _run(args, sdk, clock, live_factory)


if __name__ == "__main__":
    raise SystemExit(main())
