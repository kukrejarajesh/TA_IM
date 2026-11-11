"""Manual client for the news layer (Currents -> NewsService). Hits the network; not a test.

Usage (run from the repo root, where .env lives):
    uv run python scripts/news_client.py RELIANCE.NS --as-of 2025-06-30T16:00
    uv run python scripts/news_client.py TCS.NS            # as_of = now (IST)

The key is CURRENTS_API_KEY, from the environment or else from .env (only that one value is
read, and it is never printed). `--as-of` is read as IST unless it carries its own offset.
Prints kept items, every service event (drops, dedups, gaps), and a description of the typed
NewsItem model the analyst agent receives, with a real example.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from ta_in.data.point_in_time import IST
from ta_in.news import NewsItem, NewsService
from ta_in.news.adapters import CurrentsNewsProvider


def _parse_as_of(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(IST)
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=IST)


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_key() -> tuple[str, str]:
    """Return (key, diagnostic). The key comes from the environment, else from the repo's .env.

    The diagnostic lists variable NAMES only, never values.
    """
    key = os.environ.get("CURRENTS_API_KEY", "").strip()
    if key:
        return key, ""
    if not ENV_FILE.exists():
        return "", f"no .env at {ENV_FILE}"
    values = dotenv_values(ENV_FILE, encoding="utf-8-sig")
    key = (values.get("CURRENTS_API_KEY") or "").strip()
    if key:
        return key, ""
    names = ", ".join(sorted(values)) or "(none)"
    present = "CURRENTS_API_KEY" in values
    return "", (
        f".env at {ENV_FILE} has names: {names}; "
        f"CURRENTS_API_KEY {'is present but empty' if present else 'is missing'}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch news for a ticker as of a date.")
    ap.add_argument("ticker", help="NSE ticker, e.g. RELIANCE.NS")
    ap.add_argument("--as-of", help="ISO datetime, IST if no offset (default: now)")
    ap.add_argument("--lookback-days", type=int, default=7, help="1-7 (Currents max range)")
    ap.add_argument("--name", help='company name to search as well, e.g. "Infosys Limited"')
    args = ap.parse_args(argv)

    key, why = _load_key()
    if not key:
        print(f"CURRENTS_API_KEY not found: {why}", file=sys.stderr)
        return 2

    as_of = _parse_as_of(args.as_of)
    events: list[tuple[str, dict[str, Any]]] = []
    service = NewsService(
        [
            CurrentsNewsProvider(
                key,
                lookback_days=args.lookback_days,
                name_resolver=(lambda _t: args.name) if args.name else None,
            )
        ],
        event_sink=lambda name, payload: events.append((name, payload)),
    )
    items = service.get_news(args.ticker, as_of=as_of)

    print(f"{args.ticker} as_of={as_of.isoformat()} -> {len(items)} item(s)\n")
    for item in items:
        when = item.published_time.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
        print(f"[{when}] {item.source}: {item.title}\n    {item.url}")
    if events:
        print("\nEvents:")
        for name, payload in events:
            detail = payload.get("error") or payload.get("title") or payload
            print(f"  {name}: {detail}")
    _print_model_description(items)
    return 0


_FIELD_NOTES = {
    "title": "headline, never empty",
    "source": "outlet or author, never empty (falls back to the provider name)",
    "published_time": "when it became public; timezone-aware, always stored in UTC",
    "url": "link to the article; the key used to remove duplicates",
    "summary": "short description from the source, or None if it gave none",
}


def _print_model_description(items: list[NewsItem]) -> None:
    """Show the typed shape the news analyst will receive, with a real example."""
    print("\n" + "=" * 78)
    print("MODEL PASSED TO THE ANALYST AGENT")
    print("=" * 78)
    print(
        "NewsService.get_news(ticker, as_of=...) returns list[NewsItem]: the items that survived\n"
        "the as_of filter and de-duplication, oldest first. [] means 'no news', never None. The\n"
        "analyst gets these typed objects, not raw API responses. A failed source is logged as a\n"
        "gap event (above) rather than an exception. NewsItem is frozen (read-only).\n"
        "Note: the news analyst itself (S2.4) is not built yet; this is the contract it will use.\n"
    )
    print("NewsItem fields:")
    for name, field in NewsItem.model_fields.items():
        kind = "required" if field.is_required() else "optional"
        annotation = getattr(field.annotation, "__name__", str(field.annotation))
        print(f"  {name:<15} {annotation:<12} {kind:<9} {_FIELD_NOTES.get(name, '')}")
    if items:
        print("\nExample (latest item, as JSON):")
        print(json.dumps(items[-1].model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
