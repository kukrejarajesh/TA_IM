"""Typed fundamentals records handed to the analyst layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

# {period label such as "2025-03-31": {line item: value}}. A missing figure is None, never NaN.
Statement = dict[str, dict[str, float | None]]


def _require_aware(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return v


class Statements(BaseModel):
    """The three yearly statements as a source returned them."""

    model_config = ConfigDict(frozen=True)

    income_statement: Statement
    balance_sheet: Statement
    cash_flow: Statement

    def is_empty(self) -> bool:
        return not (self.income_statement or self.balance_sheet or self.cash_flow)


class FundamentalsData(BaseModel):
    """Statements for a current-date run. `fetched_at` is when they were retrieved."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: datetime
    fetched_at: datetime
    income_statement: Statement
    balance_sheet: Statement
    cash_flow: Statement

    @field_validator("as_of", "fetched_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v)


class Withheld(BaseModel):
    """Fundamentals deliberately not provided, with the reason (look-ahead risk or an outage)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: datetime
    reason: str

    @field_validator("as_of")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _require_aware(v)
