"""S1.1: point-in-time guard and DataProvider interface."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from ta_in.data import (
    IST,
    DataProvider,
    PointInTimeError,
    check_not_after,
    guard_records,
    is_visible,
)

AS_OF = datetime(2026, 1, 15, 15, 30, tzinfo=IST)


def _dated(rec: datetime | date) -> datetime | date:
    return rec


def test_later_dated_record_raises() -> None:
    with pytest.raises(PointInTimeError):
        guard_records([AS_OF + timedelta(seconds=1)], AS_OF, _dated)


def test_later_dated_day_raises() -> None:
    with pytest.raises(PointInTimeError):
        guard_records([date(2026, 1, 16)], AS_OF, _dated)


def test_error_message_names_record_and_as_of() -> None:
    with pytest.raises(PointInTimeError) as exc:
        check_not_after(date(2026, 1, 16), AS_OF, what="bar")
    msg = str(exc.value)
    assert "bar" in msg
    assert "2026-01-16" in msg
    assert AS_OF.isoformat() in msg


def test_earlier_and_boundary_datetimes_pass() -> None:
    records = [AS_OF - timedelta(days=3), AS_OF]
    assert guard_records(records, AS_OF, _dated) == records


def test_date_on_as_of_ist_date_passes_and_next_date_fails() -> None:
    assert is_visible(date(2026, 1, 15), AS_OF)
    assert not is_visible(date(2026, 1, 16), AS_OF)


def test_date_uses_ist_calendar_date_of_as_of() -> None:
    as_of_utc = datetime(2026, 1, 14, 20, 0, tzinfo=UTC)  # 2026-01-15 01:30 IST
    assert is_visible(date(2026, 1, 15), as_of_utc)
    assert not is_visible(date(2026, 1, 16), as_of_utc)


def test_datetimes_compare_as_instants_across_timezones() -> None:
    # 2026-01-02 20:00 UTC is 2026-01-03 01:30 IST
    as_of = datetime(2026, 1, 3, 1, 0, tzinfo=IST)
    assert not is_visible(datetime(2026, 1, 2, 20, 0, tzinfo=UTC), as_of)
    assert is_visible(datetime(2026, 1, 2, 19, 0, tzinfo=UTC), as_of)


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        check_not_after(date(2026, 1, 1), datetime(2026, 1, 15))
    with pytest.raises(ValueError, match="timezone-aware"):
        is_visible(date(2026, 1, 1), datetime(2026, 1, 15))


def test_naive_record_datetime_is_treated_as_ist() -> None:
    assert is_visible(datetime(2026, 1, 15, 15, 30), AS_OF)
    assert not is_visible(datetime(2026, 1, 15, 15, 31), AS_OF)


def test_one_bad_record_in_mixed_list_raises() -> None:
    records = [date(2026, 1, 10), datetime(2026, 1, 14, tzinfo=IST), date(2026, 1, 20)]
    with pytest.raises(PointInTimeError):
        guard_records(records, AS_OF, _dated)


def test_valid_records_returned_unchanged_in_order() -> None:
    records = [date(2026, 1, 12), date(2026, 1, 10), date(2026, 1, 15)]
    assert guard_records(records, AS_OF, _dated) == records


def test_empty_input_returns_empty_list() -> None:
    assert guard_records([], AS_OF, _dated) == []


def test_guard_accepts_one_shot_iterable() -> None:
    gen = (d for d in [date(2026, 1, 1), date(2026, 1, 2)])
    assert guard_records(gen, AS_OF, _dated) == [date(2026, 1, 1), date(2026, 1, 2)]


def test_guard_uses_key_function() -> None:
    rows = [{"d": date(2026, 1, 1)}, {"d": date(2026, 2, 1)}]
    with pytest.raises(PointInTimeError):
        guard_records(rows, AS_OF, lambda r: r["d"])


def test_provider_methods_require_keyword_only_as_of() -> None:
    methods = [
        (name, fn)
        for name, fn in inspect.getmembers(DataProvider, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert {name for name, _ in methods} >= {"prices", "news", "fundamentals", "identity"}
    for name, fn in methods:
        param = inspect.signature(fn).parameters.get("as_of")
        assert param is not None, f"{name} has no as_of"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert param.default is inspect.Parameter.empty, name


def test_fake_provider_satisfies_protocol() -> None:
    class Fake:
        def prices(self, ticker: str, *, as_of: datetime) -> list[Any]:
            return []

        def news(self, ticker: str, *, as_of: datetime) -> list[Any]:
            return []

        def fundamentals(self, ticker: str, *, as_of: datetime) -> Any:
            return None

        def identity(self, ticker: str, *, as_of: datetime) -> Any:
            return None

    assert isinstance(Fake(), DataProvider)
