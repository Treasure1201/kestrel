"""Text helpers shared by the parsers, the scanner and the chunker."""

from __future__ import annotations

import re
from collections.abc import Sequence

_INLINE_SPACE = re.compile(r"[ \t\u00a0\u2007\u202f]+")


def clean_text(text: str) -> str:
    """Collapse runs of spaces, strip each line and drop empty lines."""
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in text.replace("\r", "\n").split("\n"))
    return "\n".join(line for line in lines if line)


def render_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Plain-text rendering of a table: one line per row, cells separated by ` | `."""
    lines = [" | ".join(clean_text(cell) for cell in line) for line in (header, *rows)]
    return "\n".join(line for line in lines if line.replace("|", "").strip())
