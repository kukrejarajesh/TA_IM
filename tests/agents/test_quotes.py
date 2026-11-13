"""The shared verbatim-quote check used by the research and portfolio managers (offline)."""

from __future__ import annotations

import pytest

from ta_in.agents.quotes import MIN_QUOTE_CHARS, quoted_sides

GROUPS = {
    "a": ["Upside is large and the order book supports a bigger position."],
    "b": ["A half-size entry balances the order book against stretched valuation."],
    "c": ["Debt is rising and a drawdown here would be costly for the book."],
}
A = "Upside is large and the order book supports a bigger position"
B = "A half-size entry balances the order book against stretched valuation"
C = "Debt is rising and a drawdown here would be costly for the book"


def test_each_group_is_reported_separately() -> None:
    assert quoted_sides(f'"{A}" and "{C}"', GROUPS) == {"a": True, "b": False, "c": True}
    assert quoted_sides(f'"{A}" "{B}" "{C}"', GROUPS) == {"a": True, "b": True, "c": True}
    assert quoted_sides("no quotes at all", GROUPS) == {"a": False, "b": False, "c": False}


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        (f'"{A}"', True),
        (f"“{A}”", True),  # curly quotes
        (f'"{A.upper()}"', True),  # case
        ('"Upside is large   and the order book\nsupports a bigger position"', True),  # whitespace
        ('"Upside is large"', False),  # shorter than the minimum
        ('"Upside is huge and the order book backs a much bigger position"', False),  # paraphrase
        (f'"{B}"', False),  # a quote from another group does not count
        (A, False),  # not in quotes at all
    ],
)
def test_matching_rules(text: str, ok: bool) -> None:
    assert quoted_sides(text, GROUPS)["a"] is ok


def test_the_minimum_is_twenty_characters() -> None:
    assert MIN_QUOTE_CHARS == 20
    exactly = "x" * MIN_QUOTE_CHARS
    assert quoted_sides(f'"{exactly}"', {"g": [exactly]}) == {"g": True}
    assert quoted_sides(f'"{exactly[:-1]}"', {"g": [exactly]}) == {"g": False}


def test_a_quote_may_sit_inside_any_turn_of_the_group() -> None:
    groups = {"g": ["first turn here, nothing relevant", "second turn has the wanted passage"]}
    assert quoted_sides('"the wanted passage"', {"g": groups["g"]}) == {"g": False}  # 17 chars
    assert quoted_sides('"second turn has the wanted passage"', groups) == {"g": True}


def test_an_empty_group_is_never_quoted() -> None:
    assert quoted_sides(f'"{A}"', {"a": []}) == {"a": False}
