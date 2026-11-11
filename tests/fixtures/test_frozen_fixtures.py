"""S1.8: snapshots exist for every fixture cell, and tests load only from them (network off).

Every check runs twice: once against a snapshot directory generated here from fakes (always), and
once against the committed real snapshots under tests/fixtures (skipped until the freeze has run).
"""

from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ta_in.data import IST, DataProvider
from ta_in.fixtures import (
    FixtureMissingError,
    FixtureSet,
    FrozenDataProvider,
    Manifest,
    load_fixture_set,
)
from ta_in.fixtures.models import cell_path, identity_path, prices_path
from ta_in.fundamentals import FundamentalsData, Withheld

REAL_ROOT = Path(__file__).parent
FIXTURE_SET_PATH = REAL_ROOT / "fixture_set.json"
MANIFEST_PATH = REAL_ROOT / "manifest.json"


@pytest.fixture(params=["generated", "committed"])
def frozen_root(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    if request.param == "committed":
        if not MANIFEST_PATH.exists():
            pytest.skip("real snapshots not frozen yet (run `freeze-fixtures`)")
        return REAL_ROOT
    from test_freeze_offline import _freeze  # fakes; same freeze code path as the real command

    _freeze(tmp_path)
    return tmp_path


@pytest.fixture
def manifest(frozen_root: Path) -> Manifest:
    return Manifest.model_validate_json((frozen_root / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted while loading frozen fixtures")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# --- criterion 1: snapshots exist for every fixture cell --------------------------------


def test_every_fixture_cell_has_all_its_snapshot_files(
    frozen_root: Path, manifest: Manifest
) -> None:
    assert manifest.failures == []
    fixture_set = _fixture_set(frozen_root)
    expected_past = {(c.ticker, c.as_of.date()) for c in fixture_set.past_cells()}
    have = {(c.ticker, c.as_of.astimezone(IST).date()) for c in manifest.cells}
    assert expected_past <= have, f"missing cells: {sorted(expected_past - have)}"
    if fixture_set.include_current:
        current = [c for c in manifest.cells if (c.ticker, c.as_of.date()) not in expected_past]
        assert {c.ticker for c in current} == set(fixture_set.tickers)
    for ticker in fixture_set.tickers:
        assert prices_path(frozen_root, ticker).exists(), ticker
        assert identity_path(frozen_root, ticker).exists(), ticker
    for cell in manifest.cells:
        assert cell_path(frozen_root, cell.ticker, cell.as_of).exists(), cell


def test_manifest_matches_the_files_on_disk(frozen_root: Path, manifest: Manifest) -> None:
    on_disk = sorted(p.as_posix() for p in (frozen_root / "snapshots" / "cells").rglob("*.json"))
    listed = sorted(cell_path(frozen_root, c.ticker, c.as_of).as_posix() for c in manifest.cells)
    assert on_disk == listed


# --- criterion 2: tests load only from snapshots, with the network off ------------------


def test_every_cell_loads_through_the_frozen_provider_with_the_network_off(
    frozen_root: Path, manifest: Manifest, block_network: None
) -> None:
    provider = FrozenDataProvider(frozen_root)
    assert isinstance(provider, DataProvider)
    for cell in manifest.cells:
        assert provider.identity(cell.ticker, as_of=cell.as_of).ticker == cell.ticker
        assert provider.prices(cell.ticker, as_of=cell.as_of)
        news = provider.news(cell.ticker, as_of=cell.as_of)
        assert all(item.published_time <= cell.as_of for item in news)
        assert isinstance(
            provider.fundamentals(cell.ticker, as_of=cell.as_of), (FundamentalsData, Withheld)
        )


def test_prices_never_include_a_bar_after_as_of(frozen_root: Path, manifest: Manifest) -> None:
    provider = FrozenDataProvider(frozen_root)
    for cell in manifest.cells:
        bars = provider.prices(cell.ticker, as_of=cell.as_of)
        assert max(b.date for b in bars) <= cell.as_of.astimezone(IST).date()


def test_past_cells_are_withheld_and_current_cells_have_statements(
    frozen_root: Path, manifest: Manifest
) -> None:
    provider = FrozenDataProvider(frozen_root)
    newest = max(c.as_of for c in manifest.cells)
    for cell in manifest.cells:
        got = provider.fundamentals(cell.ticker, as_of=cell.as_of)
        if cell.as_of == newest:
            assert isinstance(got, FundamentalsData)
        else:
            assert isinstance(got, Withheld)


def test_a_cell_that_was_never_frozen_raises_instead_of_calling_out(
    frozen_root: Path, block_network: None
) -> None:
    provider = FrozenDataProvider(frozen_root)
    with pytest.raises(FixtureMissingError):
        provider.news("AAA.NS", as_of=datetime(2020, 1, 1, 16, 0, tzinfo=IST))


def test_naive_as_of_is_rejected(frozen_root: Path, manifest: Manifest) -> None:
    provider = FrozenDataProvider(frozen_root)
    cell = manifest.cells[0]
    with pytest.raises(ValueError):
        provider.prices(cell.ticker, as_of=datetime(2026, 10, 21, 16, 0))


def _fixture_set(frozen_root: Path) -> FixtureSet:
    if frozen_root == REAL_ROOT:
        return load_fixture_set(FIXTURE_SET_PATH)
    from test_freeze_offline import FIXTURE_SET

    return FIXTURE_SET
