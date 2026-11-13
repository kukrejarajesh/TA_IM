"""Verbatim-quote check shared by the managers that must cite a debate.

A passage counts as a quote of a group (for example the bull, or the conservative risk analyst) when
it sits in double quotes (straight or curly), is at least `MIN_QUOTE_CHARS` characters long, and
appears word for word inside one of that group's turns. Comparison ignores case and runs of
whitespace, so line breaks and capitalisation do not matter, but a paraphrase does. The length
floor stops a stray short phrase from passing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

MIN_QUOTE_CHARS = 20
_QUOTED = re.compile(r'["“]([^"“”]+)["”]')


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def quoted_sides(text: str, groups: Mapping[str, Sequence[str]]) -> dict[str, bool]:
    """For each group name: does `text` quote that group word for word?"""
    quotes = [_normal(q) for q in _QUOTED.findall(text)]
    quotes = [q for q in quotes if len(q) >= MIN_QUOTE_CHARS]
    result: dict[str, bool] = {}
    for name, turns in groups.items():
        normal = [_normal(t) for t in turns]
        result[name] = any(q in turn for q in quotes for turn in normal)
    return result
