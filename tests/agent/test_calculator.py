"""The safe decimal calculator: valid input, invalid input, hostile input and rounding."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kestrel.agent.calculator import CalculationError, evaluate, format_decimal, normalise_expression


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0.75 * 50000", "37500"),
        ("R50 000 * 3", "150000"),
        ("50,000 + 1,000", "51000"),
        ("75% of 50000", "37500"),
        ("75% * 50000", "37500"),
        ("min(3 * 400000, 1000000)", "1000000"),
        ("max(1, 2, 3)", "3"),
        ("(10 + 20) / 4", "7.5"),
        ("-5 + 2", "-3"),
        ("3 x 4", "12"),
        ("0.1 + 0.2", "0.3"),
        ("R 1 000 000 / 12", "83333.33"),
    ],
)
def test_valid_expressions(expression: str, expected: str) -> None:
    assert evaluate(expression).text == expected


def test_rounding_is_half_up_to_two_places_and_only_at_the_end() -> None:
    assert evaluate("1 / 3").text == "0.33"
    assert evaluate("2 / 3").text == "0.67"
    assert evaluate("0.125 + 0").text == "0.13"  # half up, not banker's rounding
    assert evaluate("round(2.345, 2)").text == "2.35"
    assert evaluate("round(2.5)").text == "3"
    exact = evaluate("1 / 3 * 3")
    assert exact.text == "1"  # intermediate values keep full precision


def test_operands_and_steps_are_recorded_for_tracing() -> None:
    result = evaluate("75% of 50000")
    assert Decimal("75") in result.operands and Decimal("0.75") in result.operands
    assert Decimal("50000") in result.operands
    assert Decimal(100) not in result.operands
    assert evaluate("round(10 / 3, 2)").operands == (Decimal(10), Decimal(3))
    assert Decimal("3.333333333333333333333333333") in evaluate("10 / 3 + 1").steps


@pytest.mark.parametrize(
    "expression",
    ["", "   ", "1 +", "2 ** 3", "abs(-3)", "1 / 0", "round(1.5, 0.5)", "round(1, 2, 3)", "min()", "5 % 2", "1 < 2", "[1, 2]"],
)
def test_invalid_expressions_raise_clear_errors(expression: str) -> None:
    with pytest.raises(CalculationError) as info:
        evaluate(expression)
    assert str(info.value)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "open('x')",
        "(1).__class__",
        "x + 1",
        "max.__self__",
        "min(key=abs)",
        "9 ** 9 ** 9",
        "1e999",
        "9" * 400,
        "(" * 200 + "1" + ")" * 200,
        "lambda: 1",
        "[c for c in ()]",
    ],
)
def test_hostile_input_is_refused(expression: str) -> None:
    with pytest.raises(CalculationError):
        evaluate(expression)


def test_huge_values_are_refused() -> None:
    with pytest.raises(CalculationError):
        evaluate("999999999999 * 999999999999")


def test_normalise_and_format() -> None:
    assert normalise_expression("R1 000 000 x 2") == "1000000 * 2"
    assert format_decimal(Decimal("37500.00")) == "37500"
    assert format_decimal(Decimal("-0.00")) == "0"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("min(100,200)", "100"),
        ("max(100,200)", "200"),
        ("max(1 000, 2 000)", "2000"),
        ("round(1234.567, 2)", "1234.57"),
        ("round(1234.567,2)", "1234.57"),
        ("25,000,000 * 2", "50000000"),
        ("2 * min(1 000,(500 + 1))", "1002"),
    ],
)
def test_commas_inside_function_calls_separate_arguments(expression: str, expected: str) -> None:
    assert evaluate(expression).text == expected


def test_comma_grouping_inside_a_call_is_refused_with_advice() -> None:
    with pytest.raises(CalculationError, match="spaces"):
        evaluate("min(25,000, 30000)")
