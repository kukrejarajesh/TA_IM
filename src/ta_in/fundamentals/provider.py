"""The contract a fundamentals source implements. One adapter per source."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ta_in.fundamentals.models import Statements


class FundamentalsUnavailableError(Exception):
    """The source answered but has no statements for this company."""


@runtime_checkable
class FundamentalsProvider(Protocol):
    """A single fundamentals source.

    Statements from a source carry no announcement dates, so they describe "now". A provider is
    therefore only asked for current-date runs: `FundamentalsService` answers past dates with
    `Withheld` before any provider is called. A provider must RAISE when it cannot deliver.
    """

    name: str

    def get_fundamentals(self, ticker: str, *, as_of: datetime) -> Statements: ...
