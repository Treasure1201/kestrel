"""Number and text normalisation, including the false-alarm cases."""

from __future__ import annotations

import pytest

from eval.normalise import clauses, contains, locator_key, normalise


@pytest.mark.parametrize(
    "text",
    [
        "R25 000 000", "R25 000 000", "R25 000 000", "R 25 000 000", "R25,000,000",
        "25000000", "R25 million", "R25m", "R 25 million", "twenty-five million", "R25 000 000.00", "ZAR25000000",
    ],
)
def test_twenty_five_million_spellings_are_equal(text: str) -> None:
    assert contains(normalise(f"The maximum is {text}."), "R25 000 000")


@pytest.mark.parametrize("text", ["R5 million", "R5m", "R5 000 000", "R5,000,000", "R 5 000 000", "five million rand"])
def test_five_million(text: str) -> None:
    assert contains(normalise(text), "R5 000 000")


def test_decimal_magnitudes_and_k() -> None:
    assert normalise("R2.5m") == "2500000"
    assert normalise("R1,5 million") == "1500000"
    assert normalise("R30k") == "30000"


@pytest.mark.parametrize(
    ("answer", "variant", "expected"),
    [
        ("six months", "6 month", True),
        ("6 months", "6 month", True),
        ("a 6-month waiting period", "6 month", True),
        ("16 months", "6 month", False),
        ("R5 000 000", "R5 000", False),  # grouped digits must not be read as a shorter number
        ("R50 000 000", "R5 000 000", False),
        ("R5 000 0000", "R5 000 000", False),
        ("85", "8", False),
        ("up to 8 members", "8", True),
        ("eight members", "8", True),
        ("eighty-five", "85", True),
        ("ten working days", "10 working day", True),
        ("110 working days", "10 working day", False),
        ("the last three payslips", "re:3[^.;]{0,25}pay ?slip", True),
        ("R42 500.00 per month", "R42 500", True),
        ("R42,500", "R42 500", True),
        ("R142 500", "R42 500", False),
        ("75 per cent", "75%", True),
        ("75 %", "75%", True),
        ("the member's ID", "member's id", True),
        ("the member’s ID", "member's id", True),
        ("Recovery", "recover", True),
        ("claim", "aim", False),
        ("version 2026.1", "2026", False),  # a decimal is a different number
        ("the 2026 guide.", "2026", True),
        ("section 2.2", "2", False),
    ],
)
def test_contains(answer: str, variant: str, expected: bool) -> None:
    assert contains(normalise(answer), variant) is expected


def test_clauses_split_on_sentences_semicolons_and_but() -> None:
    parts = clauses("It is R25 million; the 2025 guide said R20 million. It was raised, but not by much")
    assert parts == ["it is 25000000", "the 2025 guide said 20000000", "it was raised", "not by much"]


def test_locator_key_ignores_quotes_and_case() -> None:
    assert locator_key('Section 2.2 “Maximum Cover”') == locator_key('section 2.2 "Maximum cover"')
