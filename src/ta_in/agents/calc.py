"""A safe calculator for the market analyst: arithmetic over verified values only.

The analyst may need a price it was not shown (the gap to a moving average, a stop set from the ATR,
a Fibonacci level, the average of recent closes). It asks this calculator, and because the answer is
a tool result the report check accepts it. To stop the calculator from laundering an invented number
(`calculate("1230")` would otherwise "show" 1230), every numeric literal in an expression must be

- a VERIFIED value: it equals a snapshot price or a tool-shown value, rounded to the decimals the
  literal is written with (the same rule the report check uses), or
- a SMALL CONSTANT: an integer from 0 to 100, or a decimal under 10 (multipliers, periods and
  ratios such as 2, 14, 0.5, 0.382, 1.5),

and at least one literal must be verified. The formula is echoed back, so an offset such as
`close + 47` stays visible. Parsing uses `ast` with a whitelist; nothing is ever passed to `eval`.
Allowed: numbers, `+ - * /`, unary minus and plus, parentheses, and `min`, `max`, `avg`, `abs`.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

MAX_LENGTH = 200
MAX_NODES = 60
MAX_ABS_RESULT = 1e9
_FUNCTIONS = {"min", "max", "avg", "abs"}

# True when a literal with this value, written with this many decimals, is a shown value.
Verifier = Callable[[float, int], bool]


class CalcError(ValueError):
    """The expression is not allowed or not computable. The message says what to change."""


@dataclass(frozen=True)
class CalcResult:
    value: float
    formula: str


def matches_shown(value: float, figure: float, places: int) -> bool:
    """Does `figure`, written with `places` decimals, equal the shown `value`?

    It matches when the figure is the value rounded OR truncated to those decimals (a model often
    cuts 1211.9955 to 1211.99 instead of rounding to 1212.00; it is the same number). It does not
    match a figure that is off in any digit, such as 1188.99 for 1188.98.
    """
    if round(value, places) == figure:
        return True
    truncated = Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
    return float(truncated) == figure


def _is_small_constant(value: float, decimals: int) -> bool:
    if decimals == 0:
        return 0 <= value <= 100
    return abs(value) < 10


def _decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


class _Evaluator:
    def __init__(self, source: str, verifier: Verifier) -> None:
        self._source = source
        self._verifier = verifier
        self._nodes = 0
        self.verified_operands = 0

    def run(self, node: ast.expr) -> float:
        self._nodes += 1
        if self._nodes > MAX_NODES:
            raise CalcError(f"the expression is too complex (more than {MAX_NODES} parts)")
        if isinstance(node, ast.Constant):
            return self._constant(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            value = self.run(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, ast.Add | ast.Sub | ast.Mult | ast.Div
        ):
            left, right = self.run(node.left), self.run(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise CalcError("division by zero")
            return left / right
        if isinstance(node, ast.Call):
            return self._call(node)
        raise CalcError(
            "only numbers, + - * /, parentheses and min, max, avg, abs are allowed "
            f"(found {type(node).__name__})"
        )

    def _constant(self, node: ast.Constant) -> float:
        value = node.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CalcError(f"only numbers are allowed (found {value!r})")
        text = ast.get_source_segment(self._source, node) or str(value)
        if "e" in text.lower():
            raise CalcError(f"write {text} without scientific notation")
        number = float(value)
        decimals = _decimals(text)
        if self._verifier(number, decimals):
            self.verified_operands += 1
        elif not _is_small_constant(number, decimals):
            raise CalcError(
                f"{text} is not a value shown by the snapshot or a tool (small constants up to "
                "100, or decimals under 10, are allowed); use only values you were shown"
            )
        return number

    def _call(self, node: ast.Call) -> float:
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise CalcError("only the functions min, max, avg and abs are allowed")
        if node.keywords or not node.args:
            raise CalcError(
                f"{node.func.id}() takes plain values, for example {node.func.id}(1, 2)"
            )
        args = [self.run(arg) for arg in node.args]
        if node.func.id == "abs":
            if len(args) != 1:
                raise CalcError("abs() takes exactly one value")
            return abs(args[0])
        if node.func.id == "min":
            return min(args)
        if node.func.id == "max":
            return max(args)
        return sum(args) / len(args)


def evaluate(expression: str, verifier: Verifier) -> CalcResult:
    """Compute `expression`, or raise `CalcError` saying why it is not allowed."""
    formula = expression.strip()
    if not formula:
        raise CalcError("the expression is empty")
    if len(formula) > MAX_LENGTH:
        raise CalcError(f"the expression is too long (over {MAX_LENGTH} characters)")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        raise CalcError(
            "not a valid expression (no thousands separators or units; example: 1188.98 - 1141.80)"
        ) from None
    evaluator = _Evaluator(formula, verifier)
    try:
        value = evaluator.run(tree.body)
    except RecursionError:
        raise CalcError("the expression is too deeply nested") from None
    if evaluator.verified_operands == 0:
        raise CalcError(
            "the expression needs at least one verified value from the snapshot or a tool; "
            "constants alone are not enough"
        )
    if not math.isfinite(value) or abs(value) >= MAX_ABS_RESULT:
        raise CalcError("the result is too large or not a number")
    return CalcResult(value=value, formula=formula)
