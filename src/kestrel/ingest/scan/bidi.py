"""Bidirectional control characters: find them, and the text whose displayed order they change.

The controls are the embeddings and overrides U+202A to U+202E (closed by U+202C) and
the isolates U+2066 to U+2069 (closed by U+2069). Each region runs from its opening
control to the matching close, or to the end of the line.

The rule, from the Unicode bidirectional algorithm and never from what the text says:

- An override forces the display direction of every character inside it. A
  right-to-left override (U+202E) over two or more characters that are not themselves
  right-to-left, or a left-to-right override (U+202D) over two or more right-to-left
  characters, shows the reader those characters in reverse order. What a reader sees
  then differs from what is stored, so the region is excluded from searchable content.
- Embeddings and isolates, and overrides that do not change the order, only set the
  direction of text that already has one. The characters are removed and the text is
  kept, but they are still reported, because they are rare outside right-to-left text.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_PDF, _PDI = "‬", "⁩"
_EMBEDDINGS = {"‪": "left-to-right embedding", "‫": "right-to-left embedding"}
_OVERRIDES = {"‭": "left-to-right override", "‮": "right-to-left override"}
_ISOLATES = {"⁦": "left-to-right isolate", "⁧": "right-to-left isolate", "⁨": "first-strong isolate"}
_OPENERS = {**_EMBEDDINGS, **_OVERRIDES, **_ISOLATES}
CONTROLS = frozenset([*_OPENERS, _PDF, _PDI])
_RTL = {"R", "AL", "AN"}


@dataclass(frozen=True)
class BidiRegion:
    """One outermost region opened by a bidirectional control."""

    start: int
    end: int
    """Offsets in the original text, controls included."""
    opener: str
    content: str
    """The text inside, controls removed, in stored (logical) order."""
    reorders: bool
    """True when the region displays its characters in a different order than stored."""

    @property
    def description(self) -> str:
        return f"{_OPENERS.get(self.opener, 'bidirectional control')} (U+{ord(self.opener):04X})"

    @property
    def displayed(self) -> str:
        """Roughly what a reader sees: the content reversed when the region reorders it."""
        return self.content[::-1] if self.reorders else self.content


def _strip(text: str) -> str:
    return "".join(ch for ch in text if ch not in CONTROLS)


def _reorders(opener: str, content: str) -> bool:
    if opener not in _OVERRIDES:
        return False
    chars = [ch for ch in content if not ch.isspace()]
    if opener == "‮":
        return sum(1 for ch in chars if unicodedata.bidirectional(ch) not in _RTL) >= 2
    return sum(1 for ch in chars if unicodedata.bidirectional(ch) in _RTL) >= 2


def bidi_regions(text: str) -> list[BidiRegion]:
    """Every outermost region opened by a bidirectional control; an override nested inside counts for its parent."""
    regions: list[BidiRegion] = []
    stack: list[tuple[str, int]] = []
    start = 0
    reorders = False

    def close(end: int) -> None:
        nonlocal reorders
        raw = text[start:end]
        opener = raw[0]
        regions.append(BidiRegion(start, end, opener, _strip(raw), reorders or _reorders(opener, _strip(raw))))
        reorders = False

    for index, ch in enumerate(text):
        if ch == "\n" and stack:
            stack.clear()
            close(index)
        elif ch in _OPENERS:
            if not stack:
                start = index
            stack.append((ch, index))
        elif ch in {_PDF, _PDI} and stack:
            opener, at = stack.pop()
            if stack and _reorders(opener, _strip(text[at : index + 1])):
                reorders = True  # a reordering override nested in an embedding or isolate
            if not stack:
                close(index + 1)
        elif ch in {_PDF, _PDI}:
            regions.append(BidiRegion(index, index + 1, ch, "", False))  # a stray close
    if stack:
        close(len(text))
    return regions


def remove_reordered(text: str) -> tuple[str, list[BidiRegion]]:
    """`text` without the regions whose display order differs from their stored order, and every region found.

    Controls outside removed regions are left in place for the caller's format-character cleanup.
    """
    regions = bidi_regions(text)
    kept, position = [], 0
    for region in regions:
        if region.reorders:
            kept.append(text[position : region.start])
            position = region.end
    kept.append(text[position:])
    return "".join(kept), regions
