"""A verbatim quote must start and end on token boundaries, not inside another word or number."""

from __future__ import annotations

import pytest

from kestrel.verify import quote_found

SOURCE = "This is uncovered. Cover of R50 000 applies (see 2.4); the rate is 7.5% after 100 days, or R 1 000 000 in total."


@pytest.mark.parametrize(
    "quote",
    ["covered", "uncover", "over of R50 000", "0 days", "R50 00", "5% after", "00 days", "000 in total", "his is"],
)
def test_fragments_inside_words_or_numbers_are_not_found(quote: str) -> None:
    assert not quote_found(quote, SOURCE)


@pytest.mark.parametrize(
    "quote",
    [
        "This is uncovered.",
        "uncovered",
        "50 000 applies",
        "R50 000 applies",
        "Cover of R50 000",
        "7.5% after 100 days",
        "100 days",
        "(see 2.4)",
        "R1 000 000 in total",
        "1 000 000 in total",
        "the rate is 7.5%",
        "Cover of ... in total.",
    ],
)
def test_whole_tokens_are_found(quote: str) -> None:
    assert quote_found(quote, SOURCE)


def test_row_cells_need_boundaries_too() -> None:
    row = "Band: 3; Members from: 100; Members to: 249; Free cover limit (R): R 3 500 000"
    assert quote_found("3; 100; 249; R 3 500 000", row, row=True)
    assert not quote_found("3; 10; 249", row, row=True)


def test_part_of_a_decimal_is_not_found() -> None:
    assert not quote_found("7 per", "a rate of 7.5 per cent")
    assert quote_found("7.5 per cent", "a rate of 7.5 per cent")
