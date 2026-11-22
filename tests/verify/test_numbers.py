"""Number extraction and quote matching used by the evidence checks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kestrel.verify import numbers_in, quote_found
from kestrel.verify.numbers import parse_number, percent_rates


def D(value: str | int) -> Decimal:
    return Decimal(value).normalize()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("R50 000", {50000}),
        ("R50,000", {50000}),
        ("R 50 000", {50000}),
        ("R 10 000 000", {10000000}),
        ("R5 million", {5000000}),
        ("R1.5m", {1500000}),
        ("R250k", {250000}),
        ("R2 billion", {2000000000}),
        ("75%", {75}),
        ("7,5%", {D("7.5")}),
        ("three months", {3}),
        ("twelve months", {12}),
        ("the 30th day", {30}),
        ("3x annual salary", {3}),
        ("from 50-99 members", {50, 99}),
    ],
)
def test_numbers_in(text: str, expected: set[int | Decimal]) -> None:
    assert numbers_in(text) == {D(v) if not isinstance(v, Decimal) else v for v in expected}


def test_labels_list_markers_and_enumerations_are_not_numbers() -> None:
    text = "1. See c3 and c7.r4.\n2) Also [c12].\nThe options are (1) cash and (2) cover."
    assert numbers_in(text) == set()


def test_one_as_an_idiom_is_not_a_number() -> None:
    assert numbers_in("any one of the benefits") == set()
    assert numbers_in("no one is covered twice") == set()
    assert numbers_in("one month") == {D(1)}


def test_percent_rates_and_parse_number() -> None:
    assert percent_rates("75% of salary and 2.5 per cent") == {D("0.75"), D("0.025")}
    assert parse_number("37 500.00") == D(37500)
    assert parse_number("R37,500") == D(37500)
    assert parse_number("not a number") is None


def test_quote_found() -> None:
    text = "Kept within 10 working days of receiving all required documents."
    assert quote_found('"within 10 working days of receiving all required documents."', text)
    assert quote_found("within 10 working days … required documents", text)
    assert not quote_found("within ten days", text)
    assert not quote_found("..", text)
    assert not quote_found("", text)
