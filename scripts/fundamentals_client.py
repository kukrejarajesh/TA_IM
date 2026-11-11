"""Manual client for the fundamentals layer (bfinance -> FundamentalsService). Hits the network.

Usage (run from the repo root):
    uv run python scripts/fundamentals_client.py RELIANCE.NS            # as_of = now (IST)
    uv run python scripts/fundamentals_client.py RELIANCE.NS --as-of 2025-06-30T16:00   # withheld

`--as-of` is read as IST unless it carries its own offset. A past date is answered with `Withheld`
without calling bfinance. Prints the result, a summary of each statement, and a description of the
typed model the analyst agent receives, with an example.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from ta_in.data.point_in_time import IST
from ta_in.fundamentals import FundamentalsData, FundamentalsService, Statement, Withheld
from ta_in.fundamentals.adapters import BFinanceFundamentalsProvider

_FIELD_NOTES = {
    "ticker": "the NSE ticker, upper-cased",
    "as_of": "the date the run is for (timezone-aware)",
    "fetched_at": "when the statements were retrieved (UTC)",
    "income_statement": "{period: {line item: value}}, yearly",
    "balance_sheet": "{period: {line item: value}}, yearly",
    "cash_flow": "{period: {line item: value}}, yearly",
}


def _parse_as_of(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(IST)
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=IST)


def _summarise(name: str, statement: Statement) -> None:
    periods = sorted(statement, reverse=True)
    if not periods:
        print(f"  {name:<17} (empty)")
        return
    latest = statement[periods[0]]
    missing = sum(1 for v in latest.values() if v is None)
    print(
        f"  {name:<17} {len(periods)} period(s), newest {periods[0]}, "
        f"{len(latest)} line items ({missing} missing)"
    )
    for item, value in list(latest.items())[:4]:
        print(f"      {item}: {value}")


def _describe_model(result: FundamentalsData | Withheld) -> None:
    print("\n" + "=" * 78)
    print("MODEL PASSED TO THE ANALYST AGENT")
    print("=" * 78)
    print(
        "FundamentalsService.get_fundamentals(ticker, as_of=...) returns one of two typed\n"
        "objects, never raw API data and never an exception:\n"
        "  FundamentalsData  statements for a current-date run (read-only).\n"
        "  Withheld          no statements, with a reason. Past dates are always Withheld,\n"
        "                    because sources return today's statements and would leak the future.\n"
        "A missing figure is None, never NaN. The analyst must say 'withheld' when it gets\n"
        "Withheld and make no fundamental claims. Note: the fundamentals analyst (S2.5) is not\n"
        "built yet; this is the contract it will use.\n"
    )
    print("FundamentalsData fields:")
    for field in FundamentalsData.model_fields:
        print(f"  {field:<17} {_FIELD_NOTES.get(field, '')}")
    print("\nWithheld fields: ticker, as_of, reason")
    print("\nThis run returned:", type(result).__name__)
    dump = result.model_dump(mode="json")
    if isinstance(result, FundamentalsData):
        for key in ("income_statement", "balance_sheet", "cash_flow"):  # keep the example short
            periods = sorted(dump[key], reverse=True)[:1]
            dump[key] = {p: dict(list(dump[key][p].items())[:3]) for p in periods}
        print("(example below is cut to the newest period and 3 items per statement)")
    print(json.dumps(dump, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch fundamentals for a ticker as of a date.")
    ap.add_argument("ticker", help="NSE ticker, e.g. RELIANCE.NS")
    ap.add_argument("--as-of", help="ISO datetime, IST if no offset (default: now)")
    args = ap.parse_args(argv)

    as_of = _parse_as_of(args.as_of)
    service = FundamentalsService([BFinanceFundamentalsProvider()])
    result = service.get_fundamentals(args.ticker, as_of=as_of)

    print(f"{args.ticker} as_of={as_of.isoformat()} -> {type(result).__name__}\n")
    if isinstance(result, Withheld):
        print(f"Reason: {result.reason}")
    else:
        print(f"Fetched at {result.fetched_at.isoformat()}")
        _summarise("income statement", result.income_statement)
        _summarise("balance sheet", result.balance_sheet)
        _summarise("cash flow", result.cash_flow)
    _describe_model(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
