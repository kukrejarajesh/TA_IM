"""Save the data a live run used, so the run can be replayed and audited offline.

A live run reads today's data from Kite, Currents and bfinance, and that data is gone tomorrow (news
ages out, fundamentals only return the latest statements). `RecordingProvider` wraps any
`DataProvider`, passes every call through and remembers what each call returned. `write_snapshot`
then writes it in the existing fixture layout under the run's own folder:

    <run folder>/data/
        manifest.json
        snapshots/identity/<TICKER>.json
        snapshots/prices/<TICKER>.json          the bars the run saw
        snapshots/cells/<TICKER>/<date>.json    price snapshot, news, news events, fundamentals

so `FrozenDataProvider` (and `ta-in run --fixtures <run folder>/data`) load it unchanged, and a
re-run with the recorded model answers gives an identical state. Each run has its own root, so a
snapshot can never clobber another run's manifest.

Rules: the first result of each kind is kept; if a later call returns something different (live
data moving during a run) a note is recorded in the cell's errors, so a snapshot is never silently
inconsistent. A source failure is re-raised unchanged (the run still sees it) and recorded. A
snapshot is never overwritten, and it is written into a temp folder and renamed, so a crash leaves
no half-written snapshot. This module never reads the clock (`frozen_at` is passed in), makes no
network call and uses no model.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from ta_in.data.identity import CompanyIdentity
from ta_in.data.price_cache import PriceBar
from ta_in.data.provider import DataProvider
from ta_in.data.snapshot import PriceSnapshot, build_snapshot
from ta_in.fixtures.models import (
    CellSnapshot,
    Manifest,
    ManifestCell,
    ManifestTicker,
    PricesFile,
    cell_path,
    identity_path,
    prices_path,
)
from ta_in.fundamentals import FundamentalsData, Withheld
from ta_in.news import NewsItem
from ta_in.news.service import EventSink
from ta_in.reports import validate_ticker

T = TypeVar("T")


def _key(ticker: str) -> str:
    return ticker.strip().upper()


@dataclass(frozen=True)
class Recorded:
    """What a run saw for one ticker. `None` means that kind of data was never requested."""

    as_of: datetime
    identity: CompanyIdentity | None
    prices: list[PriceBar] | None
    news: list[NewsItem] | None
    fundamentals: FundamentalsData | Withheld | None
    events: list[dict[str, Any]]
    errors: list[str]


class RecordingProvider:
    """A `DataProvider` that delegates to the provider `make_inner` builds and records the results.

    `make_inner` receives the news-event sink so the news events the inner provider logs are
    saved too: `RecordingProvider(lambda sink: LiveDataProvider(sources, event_sink=sink))`.
    """

    def __init__(self, make_inner: Callable[[EventSink], DataProvider]) -> None:
        self._lock = threading.Lock()
        self._identity: dict[str, CompanyIdentity] = {}
        self._prices: dict[str, list[PriceBar]] = {}
        self._news: dict[str, list[NewsItem]] = {}
        self._fundamentals: dict[str, FundamentalsData | Withheld] = {}
        self._as_of: datetime | None = None
        self._events: list[dict[str, Any]] = []
        self.divergences: list[str] = []
        self.errors: list[str] = []
        self._inner = make_inner(self.sink)

    def sink(self, name: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._events.append({"event": name, **payload})

    # --- recording helpers -------------------------------------------------------------

    def _remember(self, store: dict[str, T], key: str, kind: str, value: T) -> None:
        with self._lock:
            if key not in store:
                store[key] = value
            elif store[key] != value:
                note = f"{kind} for {key} changed between calls; the first result was recorded"
                if note not in self.divergences:
                    self.divergences.append(note)

    def _note_as_of(self, as_of: datetime) -> None:
        with self._lock:
            if self._as_of is None:
                self._as_of = as_of
            elif self._as_of != as_of:
                note = "as_of differed between calls; the first one was recorded"
                if note not in self.divergences:
                    self.divergences.append(note)

    def _fail(self, kind: str, key: str, err: Exception) -> None:
        line = f"{kind} failed for {key}: {type(err).__name__}: {str(err)[:200]}"
        with self._lock:
            if line not in self.errors:
                self.errors.append(line)

    # --- DataProvider ------------------------------------------------------------------

    def identity(self, ticker: str, *, as_of: datetime) -> CompanyIdentity:
        key = _key(ticker)
        try:
            found = self._inner.identity(ticker, as_of=as_of)
        except Exception as err:
            self._fail("identity", key, err)
            raise
        self._note_as_of(as_of)
        self._remember(self._identity, key, "identity", found)
        return found

    def prices(self, ticker: str, *, as_of: datetime) -> Sequence[PriceBar]:
        key = _key(ticker)
        try:
            bars = list(self._inner.prices(ticker, as_of=as_of))
        except Exception as err:
            self._fail("prices", key, err)
            raise
        self._note_as_of(as_of)
        self._remember(self._prices, key, "prices", bars)
        return bars

    def news(self, ticker: str, *, as_of: datetime) -> Sequence[NewsItem]:
        key = _key(ticker)
        try:
            items = list(self._inner.news(ticker, as_of=as_of))
        except Exception as err:
            self._fail("news", key, err)
            raise
        self._note_as_of(as_of)
        self._remember(self._news, key, "news", items)
        return items

    def fundamentals(self, ticker: str, *, as_of: datetime) -> FundamentalsData | Withheld:
        key = _key(ticker)
        try:
            result = self._inner.fundamentals(ticker, as_of=as_of)
        except Exception as err:
            self._fail("fundamentals", key, err)
            raise
        self._note_as_of(as_of)
        self._remember(self._fundamentals, key, "fundamentals", result)
        return result

    # --- reading back ------------------------------------------------------------------

    def recorded(self, ticker: str) -> Recorded:
        """What was recorded for `ticker`; `ValueError` if there is nothing usable to snapshot."""
        key = _key(ticker)
        with self._lock:
            identity = self._identity.get(key)
            prices = self._prices.get(key)
            if self._as_of is None or (identity is None and prices is None):
                raise ValueError(f"nothing was recorded for {key}, so there is nothing to snapshot")
            if not prices:
                raise ValueError(
                    f"no prices were recorded for {key}; a snapshot needs the price bars"
                )
            if identity is None:
                raise ValueError(f"no identity was recorded for {key}; a snapshot needs it")
            return Recorded(
                as_of=self._as_of,
                identity=identity,
                prices=list(prices),
                news=list(self._news[key]) if key in self._news else None,
                fundamentals=self._fundamentals.get(key),
                events=[dict(e) for e in self._events],
                errors=[*self.errors, *self.divergences],
            )


def _write(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _fundamentals_kind(
    value: FundamentalsData | Withheld | None,
) -> Literal["data", "withheld", "error"]:
    if value is None:
        return "error"
    return "withheld" if isinstance(value, Withheld) else "data"


def write_snapshot(
    recorder: RecordingProvider,
    run_dir: Path,
    *,
    ticker: str,
    frozen_at: datetime,
    sources: Mapping[str, str],
    holding_period_trading_days: int = 5,
) -> Path:
    """Write `<run_dir>/data/` for `ticker` and return it.

    Raises `ValueError` (nothing written) for an unsafe ticker, a missing run folder, or nothing
    usable recorded; `FileExistsError` if `data/` already exists (it is never overwritten).
    """
    key = validate_ticker(ticker)
    if not run_dir.is_dir():
        raise ValueError(f"run folder {run_dir} does not exist")
    final = run_dir / "data"
    if final.exists():
        raise FileExistsError(f"a snapshot already exists at {final}; it is never overwritten")
    rec = recorder.recorded(key)
    bars = rec.prices or []
    assert rec.identity is not None  # recorded() guarantees it

    errors = list(rec.errors)
    price_snapshot: PriceSnapshot | None = None
    try:
        price_snapshot = build_snapshot(bars, as_of=rec.as_of)
    except Exception as err:  # recorded, as freeze-fixtures does
        errors.append(f"price snapshot failed: {type(err).__name__}: {err}")

    cell = CellSnapshot(
        ticker=key,
        as_of=rec.as_of,
        frozen_at=frozen_at,
        price_snapshot=price_snapshot,
        news=rec.news or [],
        news_events=rec.events,
        fundamentals=rec.fundamentals,
        errors=errors,
    )
    manifest = Manifest(
        frozen_at=frozen_at,
        holding_period_trading_days=holding_period_trading_days,
        sources=dict(sources),
        tickers=[
            ManifestTicker(
                ticker=key, bars=len(bars), first_bar=bars[0].date, last_bar=bars[-1].date
            )
        ],
        cells=[
            ManifestCell(
                ticker=key,
                as_of=rec.as_of,
                news_count=len(cell.news),
                fundamentals=_fundamentals_kind(rec.fundamentals),
                errors=errors,
            )
        ],
        failures=[f"{key} {rec.as_of}: {e}" for e in errors],
    )

    tmp = run_dir / f".data-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        tmp.mkdir()
        _write(identity_path(tmp, key), rec.identity)
        _write(
            prices_path(tmp, key),
            PricesFile(
                ticker=key, frozen_at=frozen_at, source=sources.get("prices", "live"), bars=bars
            ),
        )
        _write(cell_path(tmp, key, rec.as_of), cell)
        _write(tmp / "manifest.json", manifest)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final
