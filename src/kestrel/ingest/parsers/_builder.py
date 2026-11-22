"""Turn a stream of headings, paragraphs and tables into `ParsedSection`s with locators.

Every parser feeds a `SectionBuilder` in reading order. The builder keeps the heading
path, opens one prose section per heading, gives each table its own section, and
writes a human locator for every section:

- `section 4.2 "Heading"` for prose under a numbered heading, `section "Heading"` otherwise;
- `section 2 "Cover amounts", table 1` for the first table under that heading;
- `page 3, section 2 "Benefit options", table 1` when the parser tracks pages;
- an explicit locator (for example `sheet "Rates", cells A5:D9`) when the parser gives one.

The file name is not part of the locator; the caller adds it.

Span invariant relied on by the scanner: a prose section's spans, joined, give its
text before whitespace clean-up; a table section's spans are in row-major order
(header row first) and each cell's text is exactly the concatenation of its spans.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from kestrel.contracts import ParsedSection, ParsedTable, TableRow, TextSpan
from kestrel.ingest.scan.visibility import is_visible
from kestrel.ingest.text import clean_text, render_table

SPACE = TextSpan(text=" ")
NEWLINE = TextSpan(text="\n")

HEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*)$")

Grid = Sequence[Sequence[Sequence[TextSpan]]]
"""Rows of cells of spans."""


def trim_spans(spans: Sequence[TextSpan]) -> list[TextSpan]:
    """Drop empty spans and strip whitespace at both ends of the sequence."""
    out = [span for span in spans if span.text]
    while out and not out[0].text.strip():
        out.pop(0)
    while out and not out[-1].text.strip():
        out.pop()
    if out:
        out[0] = out[0].model_copy(update={"text": out[0].text.lstrip()})
        out[-1] = out[-1].model_copy(update={"text": out[-1].text.rstrip()})
    return out


def heading_locator(heading: str) -> str:
    """`section 4.2 "Title"` for a numbered heading, `section "Title"` otherwise."""
    match = HEADING_NUMBER.match(heading)
    if match:
        return f'section {match.group(1)} "{match.group(2).strip()}"'
    return f'section "{heading.strip()}"'


@dataclass(frozen=True)
class ParseResult:
    """What a format parser returns: the sections and, when the file states one, a title."""

    title: str | None
    sections: tuple[ParsedSection, ...]


@dataclass
class _Draft:
    kind: str
    heading_path: tuple[str, ...]
    base: str
    spans: list[TextSpan] = field(default_factory=list)
    grid: list[list[list[TextSpan]]] | None = None
    row_addresses: list[str | None] | None = None
    cell_addresses: list[tuple[str, ...] | None] | None = None
    pages: list[int] = field(default_factory=list)
    explicit: str | None = None


class SectionBuilder:
    """Collects sections in reading order; see the module docstring for the locator forms."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []
        self._drafts: list[_Draft] = []
        self._open: _Draft | None = None
        self._carry: list[TextSpan] = []
        self.page: int | None = None
        """Current 1-based page, set by paged parsers before feeding a page's content."""

    @property
    def heading_path(self) -> tuple[str, ...]:
        """Headings from the root down to the current position."""
        return tuple(text for _, text in self._stack)

    def heading(self, level: int, spans: Sequence[TextSpan], number: str | None = None) -> bool:
        """Start a new heading at `level` (0 is the document title).

        Only visible text forms the heading. Invisible parts are carried into the next
        section so the scanner removes and reports them; a heading with no visible
        text at all is treated as a paragraph. Returns True when a heading was started.
        """
        visible = [span for span in spans if is_visible(span)]
        text = clean_text("".join(span.text for span in visible))
        if not text:
            self.paragraph(spans)
            return False
        if number and not HEADING_NUMBER.match(text):
            text = f"{number} {text}"
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, text))
        self._open = None
        hidden = [span for span in spans if span.text.strip() and not is_visible(span)]
        if hidden:
            self._carry.extend([*hidden, NEWLINE])
        return True

    def extend_heading(self, spans: Sequence[TextSpan]) -> None:
        """Append a wrapped line to the most recent heading."""
        if not self._stack:
            self.heading(1, spans)
            return
        extra = clean_text("".join(span.text for span in spans if is_visible(span)))
        if extra:
            level, text = self._stack.pop()
            self._stack.append((level, f"{text} {extra}"))

    def paragraph(self, spans: Sequence[TextSpan], *, join: TextSpan = NEWLINE, locator: str | None = None) -> None:
        """Add a paragraph to the current prose section, opening one if needed.

        `join` separates it from the previous paragraph of the same section (use
        `SPACE` to continue a paragraph that wrapped). An explicit `locator` always
        opens a new section with that locator.
        """
        spans = trim_spans(spans)
        if not spans:
            return
        draft = self._open
        if locator is not None or draft is None or draft.kind != "prose":
            draft = self._new("prose", locator)
            self._open = draft
        elif draft.spans:
            draft.spans.append(join)
        draft.spans.extend(spans)
        self._note_page(draft)

    def table(
        self,
        grid: Grid,
        *,
        row_addresses: Sequence[str | None] | None = None,
        cell_addresses: Sequence[tuple[str, ...] | None] | None = None,
        locator: str | None = None,
    ) -> None:
        """Add a table: the first row of `grid` is the header, the rest are body rows.

        `row_addresses` and `cell_addresses`, when given, align with the body rows.
        """
        cleaned = [[trim_spans(cell) for cell in row] for row in grid]
        cleaned = [row for row in cleaned if any(cell for cell in row)]
        if not cleaned:
            return
        draft = self._new("table", locator)
        draft.grid = cleaned
        draft.row_addresses = list(row_addresses) if row_addresses is not None else None
        draft.cell_addresses = list(cell_addresses) if cell_addresses is not None else None
        self._note_page(draft)
        self._open = None

    def build(self) -> tuple[ParsedSection, ...]:
        """Finish and return the sections, numbered from 0 in reading order."""
        if self._carry:
            self._flush_carry()
        sections: list[ParsedSection] = []
        seen: dict[str, int] = {}
        tables_under: dict[str, int] = {}
        for draft in self._drafts:
            locator = self._locator(draft, tables_under)
            count = seen.get(locator, 0) + 1
            seen[locator] = count
            if count > 1:
                locator = f"{locator}, part {count}"
            section = self._section(draft, len(sections), locator)
            if section is not None:
                sections.append(section)
        return tuple(sections)

    # -- internals ---------------------------------------------------------------

    def _new(self, kind: str, explicit: str | None) -> _Draft:
        if self._carry and kind == "table":
            self._flush_carry()
        path = self.heading_path
        base = heading_locator(path[-1]) if path else "start of document"
        draft = _Draft(kind=kind, heading_path=path, base=base, explicit=explicit)
        if self._carry:
            draft.spans.extend(self._carry)
            self._carry = []
        self._drafts.append(draft)
        return draft

    def _flush_carry(self) -> None:
        carry, self._carry = self._carry, []
        draft = self._new("prose", None)
        draft.spans.extend(carry)
        self._note_page(draft)
        self._open = draft

    def _note_page(self, draft: _Draft) -> None:
        if self.page is not None and self.page not in draft.pages:
            draft.pages.append(self.page)

    def _locator(self, draft: _Draft, tables_under: dict[str, int]) -> str:
        if draft.explicit:
            return draft.explicit
        locator = draft.base
        if draft.kind == "table":
            index = tables_under.get(draft.base, 0) + 1
            tables_under[draft.base] = index
            locator = f"{locator}, table {index}"
        if draft.pages:
            first, last = min(draft.pages), max(draft.pages)
            where = f"page {first}" if first == last else f"pages {first}-{last}"
            locator = f"{where}, {locator}"
        return locator

    def _section(self, draft: _Draft, ordinal: int, locator: str) -> ParsedSection | None:
        if draft.kind == "prose":
            spans = trim_spans(draft.spans)
            text = clean_text("".join(span.text for span in spans))
            if not text:
                return None
            return ParsedSection(
                ordinal=ordinal, kind="prose", heading_path=draft.heading_path, locator=locator, text=text, spans=tuple(spans)
            )
        assert draft.grid is not None
        width = max(len(row) for row in draft.grid)
        grid = [row + [[] for _ in range(width - len(row))] for row in draft.grid]
        header = tuple("".join(span.text for span in cell) for cell in grid[0])
        rows: list[TableRow] = []
        for index, row in enumerate(grid[1:]):
            row_address = draft.row_addresses[index] if draft.row_addresses and index < len(draft.row_addresses) else None
            cells_address = draft.cell_addresses[index] if draft.cell_addresses and index < len(draft.cell_addresses) else None
            rows.append(
                TableRow(
                    cells=tuple("".join(span.text for span in cell) for cell in row),
                    address=row_address or f"row {index + 1}",
                    cell_addresses=cells_address,
                )
            )
        spans = tuple(span for row in grid for cell in row for span in cell)
        text = render_table(header, [row.cells for row in rows])
        return ParsedSection(
            ordinal=ordinal,
            kind="table",
            heading_path=draft.heading_path,
            locator=locator,
            text=text,
            spans=spans,
            table=ParsedTable(header=header, rows=tuple(rows)),
        )
