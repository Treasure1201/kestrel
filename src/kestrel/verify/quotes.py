"""Is a quote verbatim in the text it cites?

Both sides are compared after whitespace, Unicode punctuation, digit-grouping and
case normalisation (`verify.numbers.comparable`). Surrounding quotation marks and
final punctuation of the quote are ignored. A quote may skip text with an ellipsis
(`...` or the ellipsis character): every part must then appear, in order. For a table
row, a quote may also join cell values with `;`, `|` or `,` instead of the row's own
`Header: value; Header: value` form, as long as every part appears in that row.
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


def _in_order(parts: list[str], haystack: str) -> bool:
    position = 0
    for part in parts:
        found = haystack.find(part, position)
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
    return bool(cells) and all(cell in haystack for cell in cells)
