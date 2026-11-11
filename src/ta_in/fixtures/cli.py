"""`freeze-fixtures`: fetch every fixture cell once from the live sources and save it to the repo.

Needs real credentials (Kite: TA_KITE_API_KEY / TA_KITE_ACCESS_TOKEN, Currents:
CURRENTS_API_KEY, read from the environment or .env) and a Python where pandas loads.
Network access happens only here.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from ta_in.data.point_in_time import IST

DEFAULT_ROOT = Path("tests") / "fixtures"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="freeze-fixtures", description=__doc__)
    ap.add_argument("--fixture-set", type=Path, default=DEFAULT_ROOT / "fixture_set.json")
    ap.add_argument("--out", type=Path, default=DEFAULT_ROOT, help="snapshot root directory")
    ap.add_argument("--only", help="freeze a single ticker, e.g. TCS.NS")
    ap.add_argument("--force", action="store_true", help="replace existing snapshots")
    args = ap.parse_args(argv)

    # Heavy and network-facing imports stay inside main so `--help` and the tests stay light.
    from ta_in.config.settings import load_settings
    from ta_in.data import (
        IdentityProvider,
        PriceProvider,
        kite_client_from_settings,
        load_sector_map,
        make_kite_fetcher,
        make_kite_identity_resolver,
    )
    from ta_in.fixtures.freeze import freeze_fixtures
    from ta_in.fixtures.models import FixtureExistsError, load_fixture_set
    from ta_in.fundamentals import FundamentalsService
    from ta_in.fundamentals.adapters import BFinanceFundamentalsProvider
    from ta_in.news import NewsService
    from ta_in.news.adapters import CurrentsNewsProvider

    # This command only needs data credentials, but Settings insists on an LLM key being present.
    os.environ.setdefault("ANTHROPIC_API_KEY", "freeze-fixtures-does-not-use-this")
    settings = load_settings()
    if settings.currents_api_key is None or not settings.currents_api_key.get_secret_value():
        print("CURRENTS_API_KEY is not set (environment or .env).", file=sys.stderr)
        return 2

    now = datetime.now(IST).replace(microsecond=0)
    kite = kite_client_from_settings(settings)
    identity = IdentityProvider(
        make_kite_identity_resolver(kite, load_sector_map()), settings.data_cache_dir
    )
    prices = PriceProvider(make_kite_fetcher(kite), settings.data_cache_dir, source="kite")
    currents_key = settings.currents_api_key.get_secret_value()

    def company_name(ticker: str) -> str | None:
        return identity.identity(ticker, as_of=now).name

    fixture_set = load_fixture_set(args.fixture_set)
    try:
        manifest = freeze_fixtures(
            fixture_set,
            args.out,
            prices=prices,
            identity=identity,
            make_news_service=lambda sink: NewsService(
                [CurrentsNewsProvider(currents_key, name_resolver=company_name)],
                event_sink=sink,
            ),
            fundamentals=FundamentalsService([BFinanceFundamentalsProvider()]),
            now=now,
            sources={
                "prices": "kite",
                "identity": "kite",
                "news": "currents",
                "fundamentals": "bfinance",
            },
            force=args.force,
            only=args.only,
        )
    except FixtureExistsError as exc:
        print(f"Refusing to overwrite: {exc}", file=sys.stderr)
        return 2

    print(
        f"Froze {len(manifest.cells)} cell(s) for {len(manifest.tickers)} ticker(s) "
        f"into {args.out} at {manifest.frozen_at.isoformat()}"
    )
    for cell in manifest.cells:
        print(
            f"  {cell.ticker} {cell.as_of.date()}  news={cell.news_count} "
            f"fundamentals={cell.fundamentals}"
        )
    if manifest.failures:
        print("\nFAILURES (review before committing):", file=sys.stderr)
        for failure in manifest.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
