"""Point-in-time guard: nothing dated after the caller's `as_of` may be returned."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from typing import TypeVar

# India has no DST, so a fixed offset avoids needing tzdata on Windows.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

T = TypeVar("T")


class PointInTimeError(Exception):
    """A record is dated after the `as_of` the caller asked for."""


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")


def is_visible(record_date: date | datetime, as_of: datetime) -> bool:
    """True if a record dated `record_date` was knowable at `as_of`.

    A `datetime` compares as an instant (naive values are read as IST). A plain `date`
    is visible through the IST calendar date of `as_of`, inclusive.
    """
    _require_aware(as_of)
    if isinstance(record_date, datetime):
        if record_date.tzinfo is None:
            record_date = record_date.replace(tzinfo=IST)
        return record_date <= as_of
    return record_date <= as_of.astimezone(IST).date()


def check_not_after(record_date: date | datetime, as_of: datetime, *, what: str = "record") -> None:
    """Raise `PointInTimeError` if `record_date` is later than `as_of`."""
    if not is_visible(record_date, as_of):
        raise PointInTimeError(
            f"{what} dated {record_date.isoformat()} is after as_of {as_of.isoformat()}"
        )


def guard_records(
    records: Iterable[T],
    as_of: datetime,
    dated: Callable[[T], date | datetime],
) -> list[T]:
    """Return `records` as a list, raising on the first one dated after `as_of`."""
    _require_aware(as_of)
    out = list(records)
    for i, rec in enumerate(out):
        check_not_after(dated(rec), as_of, what=f"record {i}")
    return out
