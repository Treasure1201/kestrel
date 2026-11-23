"""Recognise a table whose rows cover number ranges, and find the row that covers a number.

A lookup such as "which row applies to 320?" should never depend on a model comparing
numbers by eye. `find_ranges` detects ranges deterministically; `RangeTable.lookup`
names the covering row.

Recognised (numbers are read with the evidence checks' own normalisation, so digit
grouping such as `1 000` or `1,000` and a currency prefix such as `R` are fine):

- Two adjacent columns, a lower and an upper bound: in every row the first cell is a
  number and the second is a number not smaller than it. The last row's upper bound may
  instead be open-ended: empty, `+`, or a phrase such as "and above", "or more",
  "upwards", "and over". The rows must ascend without overlapping (each lower bound is
  above the previous upper bound) and without gaps larger than 1, as bands do. The
  first qualifying pair, from the left, is used.
- One column of ranges written as text: "250 to 499", "250-499", "250 - 499",
  "1000+", "1000 or more", "1000 and above", "under 6", "below 6", "less than 6",
  "up to 5", "over 1000", "more than 1000", optionally followed by one unit word
  ("6 to 21 years"). A plain number in such a column covers just itself, but a column
  of plain numbers alone is not a range. "under" / "below" / "less than" exclude the
  bound; "over" / "more than" / "above N" exclude it too. Rows must ascend without
  overlapping.

Not recognised: ranges spread over rows in other orders (descending, overlapping or
unsorted), dates, ranges written as words ("fifty to ninety-nine"), two ranges in one
cell, and bounds in non-adjacent columns.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from kestrel.agent.calculator import format_decimal
from kestrel.contracts import TableRowView
from kestrel.verify.numbers import normalise_text, parse_number

_NUM = r"(?:(?:ZAR|R)\s?)?(?:\d{1,3}(?:[ ,']\d{3})+(?!\d)|\d+)(?:\.\d+)?"
_PLAIN_NUMBER = re.compile(rf"^{_NUM}$", re.IGNORECASE)
_OPEN_PHRASE = r"(?:\+|(?:and|or)\s+(?:above|more|over|upwards|older|greater|higher)|upwards|plus)"
_OPEN_CELL = re.compile(rf"^(?:{_OPEN_PHRASE}|-|)$", re.IGNORECASE)
_UNIT = r"(?:\s+[a-z]+)?"
_TEXT_FORMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"^({_NUM})\s*(?:to|-|until|through)\s*({_NUM}){_UNIT}$", re.IGNORECASE), "between"),
    (re.compile(rf"^({_NUM}){_UNIT}\s*{_OPEN_PHRASE}$", re.IGNORECASE), "open"),
    (re.compile(rf"^(?:under|below|less\s+than|fewer\s+than)\s+({_NUM}){_UNIT}$", re.IGNORECASE), "under"),
    (re.compile(rf"^(?:up\s+to|at\s+most|not\s+more\s+than)\s+({_NUM}){_UNIT}$", re.IGNORECASE), "up_to"),
    (re.compile(rf"^(?:over|above|more\s+than|greater\s+than)\s+({_NUM}){_UNIT}$", re.IGNORECASE), "over"),
    (re.compile(rf"^({_NUM}){_UNIT}$", re.IGNORECASE), "point"),
)
MAX_BAND_GAP = Decimal(1)


@dataclass(frozen=True)
class Band:
    """The range one row covers. A missing bound is open; `*_exclusive` marks a bound the range does not include."""

    row_number: int
    low: Decimal | None
    high: Decimal | None
    low_exclusive: bool = False
    high_exclusive: bool = False

    def covers(self, value: Decimal) -> bool:
        above = self.low is None or value > self.low or (value == self.low and not self.low_exclusive)
        below = self.high is None or value < self.high or (value == self.high and not self.high_exclusive)
        return above and below

    def describe(self) -> str:
        """The range in plain words: `250 to 499`, `1000 or more`, `under 6`, `over 1000`."""
        low = format_decimal(self.low) if self.low is not None else None
        high = format_decimal(self.high) if self.high is not None else None
        if low is None and high is None:
            return "any number"
        if low is None:
            return f"under {high}" if self.high_exclusive else f"up to {high}"
        if high is None:
            return f"over {low}" if self.low_exclusive else f"{low} or more"
        return low if low == high and not (self.low_exclusive or self.high_exclusive) else f"{low} to {high}"


@dataclass(frozen=True)
class Lookup:
    """Where a number falls in a table of ranges: in `row`, or before / after / between rows."""

    value: Decimal
    row: Band | None = None
    before: Band | None = None
    after: Band | None = None


@dataclass(frozen=True)
class RangeTable:
    """The bands of a table, one per row, in row order."""

    bands: tuple[Band, ...]

    def band_for_row(self, row_number: int) -> Band | None:
        return next((band for band in self.bands if band.row_number == row_number), None)

    def lookup(self, value: Decimal) -> Lookup:
        for band in self.bands:
            if band.covers(value):
                return Lookup(value=value, row=band)
        before = [band for band in self.bands if band.high is not None and band.high <= value]
        after = [band for band in self.bands if band.low is not None and band.low >= value]
        return Lookup(value=value, before=before[-1] if before else None, after=after[0] if after else None)


def cell_number(cell: str) -> Decimal | None:
    """A cell that is a single number (digit grouping and a currency prefix allowed), else None."""
    text = normalise_text(cell)
    if not _PLAIN_NUMBER.match(text):
        return None
    return parse_number(text)


def _ascending(bands: Sequence[Band], *, max_gap: Decimal | None) -> bool:
    for previous, current in zip(bands, bands[1:], strict=False):
        if previous.high is None or current.low is None:
            return False
        touching = previous.high_exclusive or current.low_exclusive
        if current.low < previous.high or (current.low == previous.high and not touching):
            return False
        if max_gap is not None and current.low - previous.high > max_gap:
            return False
    return True


def _pair_bands(rows: Sequence[TableRowView], first: int) -> tuple[Band, ...] | None:
    bands = []
    for index, row in enumerate(rows):
        if len(row.cells) <= first + 1:
            return None
        low = cell_number(row.cells[first])
        if low is None:
            return None
        upper = row.cells[first + 1]
        high = cell_number(upper)
        if high is None:
            if index != len(rows) - 1 or not _OPEN_CELL.match(normalise_text(upper)):
                return None
        elif high < low:
            return None
        bands.append(Band(row.row_number, low, high))
    return tuple(bands) if _ascending(bands, max_gap=MAX_BAND_GAP) else None


def text_band(row_number: int, cell: str) -> Band | None:
    """The range a cell writes as text (see the module docstring), or None."""
    text = normalise_text(cell)
    for pattern, form in _TEXT_FORMS:
        match = pattern.match(text)
        if not match:
            continue
        numbers = [parse_number(group) for group in match.groups()]
        if any(number is None for number in numbers):
            return None
        first = numbers[0]
        if form == "between":
            return Band(row_number, first, numbers[1]) if numbers[1] >= first else None
        return {
            "open": Band(row_number, first, None),
            "under": Band(row_number, None, first, high_exclusive=True),
            "up_to": Band(row_number, None, first),
            "over": Band(row_number, first, None, low_exclusive=True),
            "point": Band(row_number, first, first),
        }[form]
    return None


def _column_bands(rows: Sequence[TableRowView], column: int) -> tuple[Band, ...] | None:
    bands = []
    for row in rows:
        if len(row.cells) <= column:
            return None
        band = text_band(row.row_number, row.cells[column])
        if band is None:
            return None
        bands.append(band)
    if all(band.low == band.high and band.low is not None for band in bands):
        return None
    if any(band.low is None for band in bands[1:]) or any(band.high is None for band in bands[:-1]):
        return None
    return tuple(bands) if _ascending(bands, max_gap=None) else None


def find_ranges(rows: Sequence[TableRowView]) -> RangeTable | None:
    """The table's number ranges, or None when no column (pair) holds ranges for every row."""
    if len(rows) < 2:
        return None
    width = min(len(row.cells) for row in rows)
    for first in range(width - 1):
        bands = _pair_bands(rows, first)
        if bands:
            return RangeTable(bands)
    for column in range(width):
        bands = _column_bands(rows, column)
        if bands:
            return RangeTable(bands)
    return None
