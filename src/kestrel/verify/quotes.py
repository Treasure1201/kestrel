"""Is a quote verbatim in the text it cites?

Both sides are compared after whitespace, Unicode punctuation, digit-grouping and
case normalisation (`verify.numbers.comparable`). Surrounding quotation marks and
final punctuation of the quote are ignored. A quote may skip text with an ellipsis
(`...` or the ellipsis character): every part must then appear, in order. For a table
row, a quote may also join cell values with `;`, `|` or `,` instead of the row's own
`Header: value; Header: value` form, as long as every part appears in that row.

Every part must also start and end on a token boundary: "covered" is not found in
"uncovered", nor "10" in "100". A number may follow its currency prefix (`50 000` is
found in `R50 000`).
"""

from __future__ import annotations

import re

from kestrel.verify.numbers import comparable

MIN_PART_CHARS = 3
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.+|…|\[\.\.\.\])\s*")
_EDGE = " \"'.,;:!?()[]"
_CELL_SPLIT = re.compile(r"\s*[;|]\s*|\s+-\s+")


def _parts(quote: str) -> list[str]:
    return [part.strip(_EDGE) for part in _ELLIPSIS.split(comparable(quote)) if part.strip(_EDGE)]


_CURRENCY_BEFORE = re.compile(r"(?:^|[^\w])(?:zar|r)$")


def _is_word(char: str) -> bool:
    return char.isalnum() or char == "_"


def _bounded(haystack: str, start: int, end: int) -> bool:
    """True when haystack[start:end] does not begin or end inside a word or number (`5` inside `7.5`)."""
    if haystack[start].isdigit() and start > 1 and haystack[start - 1] in ".," and haystack[start - 2].isdigit():
        return False
    if haystack[end - 1].isdigit() and end + 1 < len(haystack) and haystack[end] in ".," and haystack[end + 1].isdigit():
        return False
    if start > 0 and _is_word(haystack[start]) and _is_word(haystack[start - 1]):
        if not (haystack[start].isdigit() and _CURRENCY_BEFORE.search(haystack[max(0, start - 4):start])):
            return False
    return not (end < len(haystack) and _is_word(haystack[end - 1]) and _is_word(haystack[end]))


def _find(haystack: str, part: str, position: int = 0) -> int:
    """The first index from `position` where `part` occurs on token boundaries, or -1."""
    found = haystack.find(part, position)
    while found >= 0 and not _bounded(haystack, found, found + len(part)):
        found = haystack.find(part, found + 1)
    return found


def _in_order(parts: list[str], haystack: str) -> bool:
    position = 0
    for part in parts:
        found = _find(haystack, part, position)
        if found < 0:
            return False
        position = found + len(part)
    return True


def quote_found(quote: str, text: str, *, row: bool = False) -> bool:
    """True when `quote` appears verbatim (after normalisation) in `text`."""
    parts = _parts(quote)
    if not parts or sum(len(part) for part in parts) < MIN_PART_CHARS:
        return False
    haystack = comparable(text)
    if _in_order(parts, haystack):
        return True
    if not row:
        return False
    cells = [cell.strip(_EDGE) for part in parts for cell in _CELL_SPLIT.split(part) if cell.strip(_EDGE)]
    return bool(cells) and all(_find(haystack, cell) >= 0 for cell in cells)
