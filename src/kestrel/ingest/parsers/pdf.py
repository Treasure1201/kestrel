"""PDF parser: page-aware text with colour, size, position and background; tables cell by cell.

Plain text extraction runs table cells together, so tables found by PyMuPDF's
layout analysis are rebuilt from the positioned text spans: every span is assigned
to the cell whose box contains its centre, and lines within a cell are joined in
reading order.

Headings are inferred from type size (clearly larger than the body size) or from a
bold line that starts with a section number. Running headers and footers (lines in
the top or bottom margin band of a page) are skipped. Text outside the page is kept
with its position so the scanner can report it; it is never silently dropped.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pymupdf

from kestrel.contracts import TextSpan
from kestrel.errors import InvalidInputError
from kestrel.ingest.parsers._builder import SPACE, ParseResult, SectionBuilder
from kestrel.ingest.scan.visibility import is_visible
from kestrel.ingest.text import clean_text

MEDIA_TYPE = "application/pdf"

MARGIN_FRACTION = 0.07
"""Top and bottom band of each page treated as running header or footer."""
HEADING_SIZE_RATIO = 1.2
"""A line at least this much larger than the body size is a heading."""
_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")
_BOLD_FLAG = 16

Box = tuple[float, float, float, float]


@dataclass
class _Line:
    bbox: Box
    spans: list[TextSpan]
    size: float
    bold: bool
    block: int


def _center(box: Sequence[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside(point: tuple[float, float], box: Sequence[float]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _hex(color: int) -> str:
    return f"{(color >> 16) & 255:02X}{(color >> 8) & 255:02X}{color & 255:02X}"


class _Page:
    """Positioned lines, filled rectangles and tables of one page."""

    def __init__(self, page: Any, number: int) -> None:
        self.number = number
        rect = page.rect
        self.width, self.height = float(rect.width), float(rect.height)
        self.top = self.height * MARGIN_FRACTION
        self.bottom = self.height * (1 - MARGIN_FRACTION)
        self.invisible: list[Box] = []
        try:
            for trace in page.get_texttrace():
                if trace.get("type") == 3 or trace.get("opacity", 1) == 0:
                    self.invisible.append(tuple(trace["bbox"]))
        except (RuntimeError, ValueError, KeyError):  # not supported by every file
            pass
        self.fills: list[tuple[float, Box, str]] = []
        for drawing in page.get_drawings():
            fill, box = drawing.get("fill"), drawing.get("rect")
            if fill is None or box is None or drawing.get("fill_opacity", 1) == 0:
                continue
            box = tuple(box)
            color = "".join(f"{round(c * 255):02X}" for c in fill[:3])
            self.fills.append(((box[2] - box[0]) * (box[3] - box[1]), box, color))
        self.fills.sort(key=lambda item: item[0])
        try:
            self.tables = list(page.find_tables().tables)
        except (RuntimeError, ValueError):
            self.tables = []
        self.lines: list[_Line] = []
        flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_MEDIABOX_CLIP
        for block in page.get_text("dict", sort=True, flags=flags, clip=pymupdf.INFINITE_RECT())["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                raw = [s for s in line["spans"] if s["text"]]
                if not raw:
                    continue
                self.lines.append(
                    _Line(
                        bbox=tuple(line["bbox"]),
                        spans=[self._span(s) for s in raw],
                        size=max(s["size"] for s in raw),
                        bold=all(s["flags"] & _BOLD_FLAG for s in raw if s["text"].strip()),
                        block=block["number"],
                    )
                )

    def _span(self, raw: dict[str, Any]) -> TextSpan:
        box = tuple(raw["bbox"])
        center = _center(box)
        background = next((color for _, rect, color in self.fills if _inside(center, rect)), None)
        return TextSpan(
            text=raw["text"],
            color=_hex(raw["color"]),
            background=background,
            font_size_pt=round(float(raw["size"]), 2),
            hidden=any(_overlaps(box, inv) for inv in self.invisible),
            page=self.number,
            bbox=box,
            page_size=(self.width, self.height),
        )

    def in_margin(self, line: _Line) -> bool:
        """True for running header or footer lines (inside the page, in the margin band)."""
        x0, y0, x1, y1 = line.bbox
        on_page = y1 > 0 and y0 < self.height and x1 > 0 and x0 < self.width
        return on_page and (y1 <= self.top or y0 >= self.bottom)

    def table_of(self, line: _Line) -> Any | None:
        center = _center(line.bbox)
        return next((t for t in self.tables if _inside(center, tuple(t.bbox))), None)

    def table_grid(self, table: Any) -> list[list[list[TextSpan]]]:
        """Rebuild a table's cells from positioned spans."""
        cells: list[list[Box | None]] = [list(row.cells) for row in table.rows]
        placed: dict[tuple[int, int], list[tuple[float, float, int, TextSpan]]] = {}
        for line_no, line in enumerate(self.lines):
            for span in line.spans:
                assert span.bbox is not None
                center = _center(span.bbox)
                for r, row in enumerate(cells):
                    hit = next((c for c, box in enumerate(row) if box is not None and _inside(center, box)), None)
                    if hit is not None:
                        placed.setdefault((r, hit), []).append((round(line.bbox[1], 1), line.bbox[0], line_no, span))
                        break
        grid: list[list[list[TextSpan]]] = []
        for r, row in enumerate(cells):
            out_row: list[list[TextSpan]] = []
            for c in range(len(row)):
                items = sorted(placed.get((r, c), []), key=lambda item: (item[0], item[1], item[2]))
                cell: list[TextSpan] = []
                last_line = None
                for _, _, line_no, span in items:
                    if last_line is not None and line_no != last_line and not cell[-1].text.endswith((" ", "\n")):
                        cell.append(SPACE)
                    cell.append(span)
                    last_line = line_no
                out_row.append(cell)
            grid.append(out_row)
        return grid


def _body_size(pages: list[_Page]) -> tuple[float, float]:
    sizes: Counter[float] = Counter()
    for page in pages:
        for line in page.lines:
            if not page.in_margin(line):
                sizes[round(line.size, 1)] += sum(len(s.text) for s in line.spans)
    if not sizes:
        return 10.0, 10.0
    return sizes.most_common(1)[0][0], max(sizes)


def parse_pdf(data: bytes) -> ParseResult:
    """Parse PDF bytes into page-aware sections and a title."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise InvalidInputError("The file is not a readable PDF.", detail=str(exc)) from exc
    if document.needs_pass:
        raise InvalidInputError("The PDF is password protected and cannot be read.")
    pages = [_Page(page, index + 1) for index, page in enumerate(document)]
    body, largest = _body_size(pages)
    builder = SectionBuilder()
    title: str | None = None
    for page in pages:
        builder.page = page.number
        emitted: set[int] = set()
        previous: tuple[str, int] | None = None
        for line in page.lines:
            if page.in_margin(line):
                continue
            table = page.table_of(line)
            if table is not None:
                if id(table) not in emitted:
                    emitted.add(id(table))
                    builder.table(page.table_grid(table))
                previous = None
                continue
            text = clean_text("".join(s.text for s in line.spans if is_visible(s)))
            numbered = _NUMBERED.match(text)
            heading = bool(text) and len(text) < 120 and (line.size >= body * HEADING_SIZE_RATIO or (line.bold and numbered))
            if heading:
                if previous == ("heading", line.block):
                    builder.extend_heading(line.spans)
                    if title is not None and len(builder.heading_path) == 1:
                        title = builder.heading_path[0]
                    continue
                if title is None and page.number == 1 and line.size >= largest:
                    level = 0
                elif numbered:
                    level = numbered.group(1).count(".") + 1
                else:
                    level = 1
                if builder.heading(level, line.spans) and level == 0:
                    title = builder.heading_path[-1]
                previous = ("heading", line.block)
                continue
            join = SPACE if previous == ("body", line.block) else None
            if join is not None:
                builder.paragraph(line.spans, join=join)
            else:
                builder.paragraph(line.spans)
            previous = ("body", line.block)
    metadata_title = (document.metadata or {}).get("title") or None
    return ParseResult(title=title or metadata_title, sections=builder.build())
