"""The market analyst's safe calculator: arithmetic over verified values only (offline)."""

from __future__ import annotations

import pytest

from ta_in.agents.calc import (
    CalcError,
    CalcLimitReached,
    CalcResult,
    Calculator,
    evaluate,
    matches_shown,
)

VERIFIED = [1188.98, 1141.8, 2529.0, 1062.1, 1150.2]


def _verified(value: float, decimals: int) -> bool:
    return any(round(v, decimals) == value for v in VERIFIED)


def calc(expression: str) -> CalcResult:
    return evaluate(expression, _verified)


# --- correct values --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1188.98 - 1141.80", 47.18),
        ("1141.80 * 1.05", 1198.89),
        ("(1188.98 + 1141.80) / 2", 1165.39),
        ("-1141.80 + 1188.98", 47.18),
        ("1188.98 - 2 * 14", 1160.98),
        ("min(1141.80, 1150.20, 1188.98)", 1141.8),
        ("max(1141.80, 1150.20, 1188.98)", 1188.98),
        ("avg(1141.80, 1150.20, 1188.98)", 1160.3267),
        ("abs(1141.80 - 1188.98)", 47.18),
        ("1141.80 * 0.382", 436.1676),
        ("1188.98 / 1141.80 - 1", 0.0413),
        ("+1141.80", 1141.8),
    ],
)
def test_values_are_computed_exactly(expression: str, expected: float) -> None:
    result = calc(expression)
    assert result.value == pytest.approx(expected, abs=1e-4)
    assert result.formula == expression


def test_an_integer_that_equals_a_verified_value_counts_as_verified() -> None:
    assert calc("2529 - 100").value == 2429.0  # 2529 is verified, 100 is a small constant


# --- the operand rule: nothing made up may be laundered through the calculator ---------


@pytest.mark.parametrize(
    ("expression", "bad"),
    [
        ("1230", "1230"),
        ("1230 - 1141.80", "1230"),
        ("1188.99 - 1141.80", "1188.99"),  # one digit off a real value
    ],
)
def test_unverified_operands_are_refused_and_named(expression: str, bad: str) -> None:
    with pytest.raises(CalcError, match=bad):
        calc(expression)


def test_a_small_constant_can_be_combined_with_a_verified_value() -> None:
    assert calc("1141.80 + 47").value == pytest.approx(
        1188.8
    )  # an offset is visible in the formula


def test_an_expression_of_constants_only_is_refused() -> None:
    for expression in ("47", "2 * 14", "100 / 4", "0.5 + 1.5"):
        with pytest.raises(CalcError, match="verified"):
            calc(expression)


@pytest.mark.parametrize("constant", ["2", "14", "100", "0.5", "0.382", "1.5", "9.99", "0"])
def test_small_constants_are_allowed_next_to_a_verified_value(constant: str) -> None:
    assert isinstance(calc(f"1141.80 * {constant}").value, float)


@pytest.mark.parametrize("constant", ["101", "250", "1230", "10.5", "47.18"])
def test_larger_constants_are_not_allowed_unless_verified(constant: str) -> None:
    with pytest.raises(CalcError, match=constant.replace(".", r"\.")):
        calc(f"1141.80 + {constant}")


def test_a_literal_is_matched_at_the_decimals_it_is_written_with() -> None:
    assert calc("1188.98 - 1141.8").value == pytest.approx(47.18)
    assert calc("1189 - 1142").value == pytest.approx(47.0)  # both match when rounded to 0 dp
    assert calc("1189.0 - 1141.80").value == pytest.approx(47.2)  # 1188.98 rounds to 1189.0 at 1 dp
    with pytest.raises(CalcError, match=r"1188\.97"):
        calc("1188.97 - 1141.80")  # at 2 dp the real value is 1188.98


@pytest.mark.parametrize(
    ("shown", "figure", "places", "ok"),
    [
        (1211.9955, 1212.0, 2, True),  # rounded
        (1211.9955, 1211.99, 2, True),  # truncated, as the real INFY run wrote it
        (1211.9955, 1212.0, 0, True),
        (1188.98, 1189.0, 1, True),
        (1188.98, 1188.98, 2, True),
        (1188.98, 1188.99, 2, False),  # off in the last digit
        (1188.98, 1188.97, 2, False),
        (1188.98, 1188.0, 0, True),  # truncated to whole rupees
        (1188.98, 1187.0, 0, False),
        (1188.98, 1190.0, 0, False),
        (2480.351, 2480.4, 1, True),  # 2480.351 rounded to one decimal
        (2480.351, 2480.5, 1, False),
    ],
)
def test_a_figure_matches_a_shown_value_rounded_or_truncated_but_not_off_by_a_digit(
    shown: float, figure: float, places: int, ok: bool
) -> None:
    assert matches_shown(shown, figure, places) is ok


# --- only safe syntax ------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('x')",
        "open('x')",
        "x + 1141.80",
        "1141.80 ** 2",
        "1141.80 // 2",
        "1141.80 % 7",
        "[1141.80]",
        "(1141.80, 1188.98)",
        "'a' + 1141.80",
        "(1141.80).real",
        "lambda: 1141.80",
        "(y := 1141.80)",
        "1141.80 if 1 else 2",
        "sum(1141.80, 1188.98)",
        "min(key=1141.80)",
        "min()",
        "1141.80 and 1188.98",
        "1141.80 < 1188.98",
        "True + 1141.80",
        "1e3 - 1141.80",
        "1,141.80 - 1",
        "",
        "   ",
        "1141.80 -",
    ],
)
def test_anything_but_plain_arithmetic_is_refused(expression: str) -> None:
    with pytest.raises(CalcError):
        calc(expression)


def test_expressions_have_a_length_and_size_limit() -> None:
    with pytest.raises(CalcError, match="long"):
        calc("1141.80" + " + 1" * 100)
    with pytest.raises(CalcError, match="complex"):
        calc("1141.80" + " + 1" * 40)


def test_nested_functions_work_within_the_limits() -> None:
    assert calc("max(min(1188.98, 1150.20), 1141.80)").value == 1150.2


# --- numeric safety --------------------------------------------------------------------


def test_division_by_zero_is_an_error_not_a_crash() -> None:
    with pytest.raises(CalcError, match="zero"):
        calc("1141.80 / 0")
    with pytest.raises(CalcError, match="zero"):
        calc("1141.80 / (1188.98 - 1188.98)")


def test_huge_or_non_finite_results_are_refused() -> None:
    big = "1141.80" + " * 100" * 6  # 1141.80e12
    with pytest.raises(CalcError, match="large"):
        calc(big)


# --- the Calculator tool (S2.5 fix) ----------------------------------------------------


def _calculator() -> Calculator:
    return Calculator([2024.0, 90828.0, -39786.0, 1211.9955])


def test_the_calculator_computes_from_shown_values_and_remembers_the_result() -> None:
    calculator = _calculator()
    out = calculator.run("2024.00 + 90828.00")
    assert out == {"expression": "2024.00 + 90828.00", "value": 92852.0}
    assert 92852.0 in calculator.values
    assert calculator.calls == 1
    assert calculator.run("92852.00 - 2024.00")["value"] == 90828.0  # a result is a shown value


def test_the_calculator_accepts_a_negative_shown_value_written_as_positive() -> None:
    assert _calculator().run("39786 - 1")["value"] == 39785.0  # -39,786 is shown


def test_the_calculator_refuses_an_unshown_number_and_names_it() -> None:
    calculator = _calculator()
    with pytest.raises(CalcError, match="1230"):
        calculator.run("1230 + 2024.00")
    with pytest.raises(CalcError, match="1230"):
        calculator.run("1230")
    with pytest.raises(CalcError, match="verified"):
        calculator.run("2 * 14")  # constants alone are not enough
    assert 1230.0 not in calculator.values and calculator.calls == 0  # a refusal costs nothing


def test_the_calculator_is_capped_and_the_cap_message_says_so() -> None:
    calculator = Calculator([2024.0], limit=3)
    for _ in range(3):
        calculator.run("2024.00 * 2")
    with pytest.raises(CalcLimitReached, match="at most 3"):
        calculator.run("2024.00 * 2")


def test_the_calculator_tool_has_a_schema_and_calls_run() -> None:
    calculator = _calculator()
    tool = calculator.tool()
    assert tool.name == "calculate"
    assert "expression" in tool.schema()["input_schema"]["properties"]
    assert (
        tool.fn(tool.input_model.model_validate({"expression": "2024.00 + 1"}))["value"] == 2025.0
    )


@pytest.mark.parametrize("expression", ["__import__('os')", "2024.00 ** 2", "x + 2024.00", ""])
def test_the_calculator_inherits_the_safe_syntax_rules(expression: str) -> None:
    with pytest.raises(CalcError):
        _calculator().run(expression)


def test_the_error_is_a_value_error_with_a_message_the_model_can_act_on() -> None:
    with pytest.raises(ValueError) as exc:
        calc("1230 - 1141.80")
    assert "1230" in str(exc.value) and "snapshot" in str(exc.value)
