"""Source numbers match exactly after normalisation and keep their sign; only calculation results may be rounded."""

from __future__ import annotations

from decimal import Decimal

import pytest
from verify.builders import SCOPE, answer, prose

from kestrel.agent.calculator import evaluate
from kestrel.verify import numbers_in, run_checks
from kestrel.verify.numbers import matches_calculated, percent_rates


def D(value: str | int) -> Decimal:
    return Decimal(value).normalize()


def _grounding(answer_text: str, source: str, calculations=(), reported=()):  # type: ignore[no-untyped-def]
    evidence = {"c1": prose("c1", source)}
    ans = answer(answer_text, [("c1", source)], calculations=list(reported))
    return [i.check for i in run_checks("What applies?", ans, evidence, SCOPE, tuple(calculations))]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-R50000", {-50000}),
        ("-R50 000", {-50000}),
        ("R-50 000", {-50000}),
        ("a change of -5%", {-5}),
        ("from 50-99 members", {50, 99}),
        ("on 2026-03-11", {2026, 3, 11}),
        ("ages 18 - 65", {18, 65}),
    ],
)
def test_signs_are_kept_and_ranges_are_not_signs(text: str, expected: set[int]) -> None:
    assert numbers_in(text) == {D(v) for v in expected}


def test_negative_percent_rate() -> None:
    assert percent_rates("-5%") == {D("-0.05")}


def test_a_close_but_different_source_number_is_not_grounded() -> None:
    assert _grounding("The fee is 0.015% of the balance.", "The fee is 0.01% of the balance.") == ["numbers_grounded"]


def test_a_sign_flip_is_not_grounded() -> None:
    assert _grounding("The adjustment is R50 000.", "The adjustment is -R50 000.") == ["numbers_grounded"]
    assert _grounding("The adjustment is -R50 000.", "The adjustment is -R50 000.") == []


@pytest.mark.parametrize(
    ("answer_text", "source"),
    [
        ("The benefit is 0.75 of salary.", "The benefit is 75% of salary."),
        ("The maximum is R5 000 000.", "The maximum is R5 million."),
        ("The limit is R1 000 000.", "The limit is R 1,000,000."),
        ("The wait is 3 months.", "The wait is three months."),
    ],
)
def test_format_normalisation_still_grounds(answer_text: str, source: str) -> None:
    assert _grounding(answer_text, source) == []


def test_calculation_results_may_be_rounded_as_shown() -> None:
    values = {D("1234.5678")}
    assert matches_calculated(values, D("1234.57"))
    assert matches_calculated(values, D("1235"))
    assert matches_calculated(values, D("1234.6"))
    assert not matches_calculated(values, D("1234.5"))
    assert not matches_calculated(values, D("1240"))


def test_a_rounded_calculation_result_is_grounded_in_the_answer() -> None:
    calc = evaluate("40000 / 3")
    source = "The monthly benefit is 40000 divided by 3."
    issues = _grounding("The benefit is R13 333.33 a month.", source, [calc], [("40000 / 3", "13333.33", "c1")])
    assert issues == []
