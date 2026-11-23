"""Generic rules that decide whether a reader could see a span of text.

The rules look only at rendering attributes, never at what the text says or which
document it came from:

- explicitly hidden (a format's hidden or vanish flag, an invisible render mode);
- covered by an opaque shape painted over it (the parser records this from paint order);
- positioned off the page;
- a font too small to read;
- too little contrast between the text colour and its *resolved* background.

The background is resolved in this order: the span's own background (run shading,
highlight, table-cell shading or spreadsheet cell fill, as recorded by the parser),
then the page, which is white unless the caller says otherwise. So white text on a
dark shaded cell is visible, while white text on a white page is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kestrel.contracts import FindingKind, TextSpan

WHITE = "FFFFFF"

MIN_READABLE_PT = 3.0
"""Text smaller than this cannot be read at normal zoom (body text is 9 to 12 pt)."""

MIN_CONTRAST = 1.5
"""Contrast ratio (WCAG formula) below which text is practically invisible.

WCAG asks for 4.5:1 for readable body text; 1.5:1 is far below that and catches
white or near-white text on white, while leaving merely pale text (grey footers,
light captions) in place.
"""

_HEX = re.compile(r"^[0-9A-F]{6}$")

# Severity order used to pick the one kind a finding is reported under.
_KIND_ORDER: tuple[FindingKind, ...] = ("hidden_text", "covered_text", "off_page", "tiny_text", "low_contrast")


def normalise_color(value: str | None) -> str | None:
    """Return `RRGGBB` upper-case hex for a colour value, or None when unknown or automatic.

    Accepts `RRGGBB`, `#RRGGBB`, `#RGB` and spreadsheet `AARRGGBB` (alpha is dropped).
    """
    if value is None:
        return None
    text = str(value).strip().lstrip("#").upper()
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    elif len(text) == 8:
        text = text[2:]
    return text if _HEX.match(text) else None


def _luminance(color: str) -> float:
    def channel(raw: int) -> float:
        c = raw / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two `RRGGBB` colours, from 1.0 (identical) to 21.0."""
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@dataclass(frozen=True)
class Invisibility:
    """One reason a span is invisible, with a short human explanation."""

    kind: FindingKind
    reason: str


def resolved_background(span: TextSpan, page_background: str = WHITE) -> str:
    """The colour actually behind the span: its own background, else the page."""
    return normalise_color(span.background) or normalise_color(page_background) or WHITE


def invisibility(span: TextSpan, page_background: str = WHITE) -> list[Invisibility]:
    """Every reason a reader could not see `span`. Whitespace-only spans are never invisible."""
    if not span.text.strip():
        return []
    reasons: list[Invisibility] = []
    if span.hidden:
        reasons.append(Invisibility("hidden_text", "marked hidden by the format"))
    if span.covered:
        reasons.append(Invisibility("covered_text", "covered by an opaque shape drawn over it"))
    if _off_page(span):
        reasons.append(Invisibility("off_page", "positioned outside the page"))
    if span.font_size_pt is not None and 0 <= span.font_size_pt < MIN_READABLE_PT:
        reasons.append(Invisibility("tiny_text", f"font size {span.font_size_pt:g} pt"))
    # An unknown or automatic text colour is not judged: word processors render
    # "automatic" text dark on light backgrounds and light on dark ones.
    foreground = normalise_color(span.color)
    if foreground is not None:
        background = resolved_background(span, page_background)
        ratio = contrast_ratio(foreground, background)
        if ratio < MIN_CONTRAST:
            reasons.append(
                Invisibility("low_contrast", f"text colour {foreground} on background {background}, contrast {ratio:.2f}:1")
            )
    return reasons


def is_visible(span: TextSpan, page_background: str = WHITE) -> bool:
    """True when a reader could see the span."""
    return not invisibility(span, page_background)


def primary_kind(reasons: list[Invisibility]) -> FindingKind:
    """The kind a finding is reported under when several reasons apply."""
    kinds = {reason.kind for reason in reasons}
    return next(kind for kind in _KIND_ORDER if kind in kinds)


def _off_page(span: TextSpan) -> bool:
    if span.bbox is None or span.page_size is None:
        return False
    x0, y0, x1, y1 = span.bbox
    width, height = span.page_size
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx < 0 or cy < 0 or cx > width or cy > height
