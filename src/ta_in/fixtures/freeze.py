"""Freeze the data for every fixture cell into the repo, using the existing providers.

No new data logic lives here: prices, identity, news and fundamentals come from the same
providers and services the agents will use, so a frozen cell is exactly what a live run would see.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from ta_in.data.identity import CompanyIdentity
from ta_in.data.price_cache import PriceBar
from ta_in.data.prices import effective_end
from ta_in.data.snapshot import PriceSnapshot, build_snapshot
from ta_in.fixtures.models import (
    CellSnapshot,
    FixtureCell,
    FixtureExistsError,
    FixtureSet,
    Manifest,
    ManifestCell,
    ManifestTicker,
    PricesFile,
    cell_path,
    identity_path,
    prices_path,
)
from ta_in.fundamentals.models import FundamentalsData, Withheld
from ta_in.news.models import NewsItem
from ta_in.news.service import EventSink, NewsService

log = logging.getLogger(__name__)


class PricesSource(Protocol):
    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]: ...


class IdentitySource(Protocol):
    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity: ...


class FundamentalsSource(Protocol):
    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld: ...


NewsServiceFactory = Callable[[EventSink], NewsService]


def _write(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _check_not_existing(root: Path, cells: list[FixtureCell], tickers: list[str]) -> None:
    targets = [prices_path(root, t) for t in tickers] + [identity_path(root, t) for t in tickers]
    targets += [cell_path(root, c.ticker, c.as_of) for c in cells]
    existing = [p for p in targets if p.exists()]
    if existing:
        shown = ", ".join(p.as_posix() for p in existing[:3])
        raise FixtureExistsError(
            f"{len(existing)} snapshot file(s) already exist (e.g. {shown}); use --force to replace"
        )


def freeze_fixtures(
    fixture_set: FixtureSet,
    out_dir: Path,
    *,
    prices: PricesSource,
    identity: IdentitySource,
    make_news_service: NewsServiceFactory,
    fundamentals: FundamentalsSource,
    now: datetime,
    sources: dict[str, str],
    force: bool = False,
    only: str | None = None,
) -> Manifest:
    """Write snapshots for every cell and a manifest. Source failures are recorded, never hidden."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    tickers = [t for t in fixture_set.tickers if only is None or t == only.upper()]
    cells = [c for c in fixture_set.cells(now) if c.ticker in tickers]
    if not force:
        _check_not_existing(out_dir, cells, tickers)

    failures: list[str] = []
    manifest_tickers: list[ManifestTicker] = []
    manifest_cells: list[ManifestCell] = []

    for ticker in tickers:
        try:
            who = identity.identity(ticker, as_of=now)
            bars = list(prices.prices(ticker, as_of=now))
            if not bars:
                raise ValueError("no price bars returned")
        except Exception as exc:  # recorded; the other tickers still freeze
            failures.append(f"{ticker}: identity/prices failed: {type(exc).__name__}: {exc}")
            log.warning("freeze: %s skipped: %s", ticker, exc)
            continue
        _write(identity_path(out_dir, ticker), who)
        _write(
            prices_path(out_dir, ticker),
            PricesFile(
                ticker=ticker, frozen_at=now, source=sources.get("prices", "unknown"), bars=bars
            ),
        )
        manifest_tickers.append(
            ManifestTicker(
                ticker=ticker, bars=len(bars), first_bar=bars[0].date, last_bar=bars[-1].date
            )
        )
        for cell in (c for c in cells if c.ticker == ticker):
            manifest_cells.append(
                _freeze_cell(out_dir, cell, bars, make_news_service, fundamentals, now)
            )

    manifest = Manifest(
        frozen_at=now,
        holding_period_trading_days=fixture_set.holding_period_trading_days,
        sources=sources,
        tickers=manifest_tickers,
        cells=manifest_cells,
        failures=failures
        + [f"{c.ticker} {c.as_of}: {e}" for c in manifest_cells for e in c.errors],
    )
    _write(out_dir / "manifest.json", manifest)
    return manifest


def _freeze_cell(
    out_dir: Path,
    cell: FixtureCell,
    bars: list[PriceBar],
    make_news_service: NewsServiceFactory,
    fundamentals: FundamentalsSource,
    now: datetime,
) -> ManifestCell:
    errors: list[str] = []
    last_visible = effective_end(cell.as_of)
    visible = [b for b in bars if b.date <= last_visible]

    snapshot: PriceSnapshot | None = None
    try:
        snapshot = build_snapshot(visible, as_of=cell.as_of)
    except Exception as exc:
        errors.append(f"price snapshot failed: {type(exc).__name__}: {exc}")

    events: list[dict[str, Any]] = []
    news: list[NewsItem] = []
    try:
        service = make_news_service(lambda name, payload: events.append({"event": name, **payload}))
        news = service.get_news(cell.ticker, as_of=cell.as_of)
    except Exception as exc:
        errors.append(f"news failed: {type(exc).__name__}: {exc}")

    funds: FundamentalsData | Withheld | None = None
    try:
        funds = fundamentals.get_fundamentals(cell.ticker, as_of=cell.as_of)
    except Exception as exc:
        errors.append(f"fundamentals failed: {type(exc).__name__}: {exc}")

    _write(
        cell_path(out_dir, cell.ticker, cell.as_of),
        CellSnapshot(
            ticker=cell.ticker,
            as_of=cell.as_of,
            frozen_at=now,
            price_snapshot=snapshot,
            news=news,
            news_events=events,
            fundamentals=funds,
            errors=errors,
        ),
    )
    kind: Literal["data", "withheld", "error"] = (
        "error" if funds is None else ("withheld" if isinstance(funds, Withheld) else "data")
    )
    return ManifestCell(
        ticker=cell.ticker,
        as_of=cell.as_of,
        news_count=len(news),
        fundamentals=kind,
        errors=errors,
    )
