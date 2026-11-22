"""XLSX parser: every sheet, table regions found by layout, header rows and cell addresses.

A sheet is split into regions: runs of non-empty rows separated by empty rows, and
within those, runs of columns separated by empty columns. In each region the first
row with two or more filled cells is the header; the rows below it are the body. A
short single-cell row above a region is its heading. Anything else is prose.

Values are shown as the cell's number format would show them, with literal prefixes
and suffixes, decimals and percentages. Thousands are grouped with a space (the
SI/ISO convention) rather than a locale-dependent comma. Cell font colour, solid fill
and font size are kept for the visibility rules; hidden sheets, rows and columns, and
the `;;;` "show nothing" number format, mark their cells hidden.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, time
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from kestrel.contracts import TextSpan
from kestrel.ingest.parsers._builder import ParseResult, SectionBuilder
from kestrel.ingest.scan.visibility import is_visible, normalise_color
from kestrel.ingest.text import clean_text

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MAX_LABEL_CHARS = 80
_CELL_JOIN = TextSpan(text=" | ")
_LOCALE_CURRENCY = re.compile(r"\[\$([^\]-]*)(?:-[^\]]*)?\]")
_BRACKETED = re.compile(r"\[[^\]]*\]")
_QUOTED = re.compile(r'"([^"]*)"')
_DIGIT_PLACEHOLDER = re.compile(r"[0#?]")


def format_number(value: float | int, number_format: str | None) -> str:
    """Render a number the way a simple Excel number format would show it."""
    fmt = (number_format or "General").split(";")[0]
    if fmt.strip().lower() in ("general", "@", ""):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return f"{value:.10g}" if isinstance(value, float) else str(value)
    fmt = _LOCALE_CURRENCY.sub(lambda m: f'"{m.group(1)}"', fmt)
    fmt = _BRACKETED.sub("", fmt)
    placeholders = [m.start() for m in _DIGIT_PLACEHOLDER.finditer(fmt)]
    if not placeholders:
        return str(value)
    start, end = placeholders[0], placeholders[-1] + 1
    numeric = fmt[start:end]
    prefix, suffix = _literal(fmt[:start]), _literal(fmt[end:])
    percent = "%" in fmt
    if percent:
        value = value * 100
        suffix = suffix.replace("%", "") + "%"
    decimals = len(re.search(r"\.([0#?]*)", numeric).group(1)) if "." in numeric else 0
    grouped = "," in numeric.split(".")[0]
    body = f"{abs(value):,.{decimals}f}" if grouped else f"{abs(value):.{decimals}f}"
    body = body.replace(",", " ")
    sign = "-" if value < 0 else ""
    return f"{sign}{prefix}{body}{suffix}".strip()


def _literal(part: str) -> str:
    part = _QUOTED.sub(lambda m: m.group(1), part)
    part = re.sub(r"\\(.)", r"\1", part)
    part = re.sub(r"_.", "", part)
    part = re.sub(r"\*.", "", part)
    return part


def format_value(value: Any, number_format: str | None) -> str:
    """Display text of one cell value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return format_number(value, number_format)
    return str(value).strip()


def _rgb(color: Any) -> str | None:
    if color is None or getattr(color, "type", None) != "rgb" or not isinstance(color.rgb, str):
        return None
    return normalise_color(color.rgb)


class _Sheet:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.hidden = ws.sheet_state != "visible"
        self.hidden_columns: set[int] = set()
        for dim in ws.column_dimensions.values():
            if dim.hidden and dim.min is not None and dim.max is not None:
                self.hidden_columns.update(range(dim.min, dim.max + 1))
        self.cells: dict[tuple[int, int], TextSpan] = {}
        for row in ws.iter_rows():
            for cell in row:
                text = format_value(cell.value, cell.number_format)
                if text:
                    self.cells[(cell.row, cell.column)] = self._span(cell, text)

    def _span(self, cell: Any, text: str) -> TextSpan:
        hidden = (
            self.hidden
            or bool(self.ws.row_dimensions[cell.row].hidden)
            or cell.column in self.hidden_columns
            or bool(re.fullmatch(r";;;?", (cell.number_format or "").strip()))
        )
        fill = cell.fill
        background = _rgb(fill.fgColor) if fill is not None and fill.patternType == "solid" else None
        font = cell.font
        return TextSpan(
            text=text,
            color=_rgb(font.color) if font is not None else None,
            background=background,
            font_size_pt=float(font.sz) if font is not None and font.sz else None,
            hidden=hidden,
        )

    def regions(self) -> list[tuple[int, int, int, int]]:
        """(first_row, last_row, first_col, last_col) of each region, in reading order."""
        rows = sorted({r for r, _ in self.cells})
        bands: list[list[int]] = []
        for r in rows:
            if bands and r == bands[-1][-1] + 1:
                bands[-1].append(r)
            else:
                bands.append([r])
        out: list[tuple[int, int, int, int]] = []
        for band in bands:
            cols = sorted({c for r, c in self.cells if band[0] <= r <= band[-1]})
            groups: list[list[int]] = []
            for c in cols:
                if groups and c == groups[-1][-1] + 1:
                    groups[-1].append(c)
                else:
                    groups.append([c])
            out.extend((band[0], band[-1], g[0], g[-1]) for g in groups)
        return out


def _range(r0: int, c0: int, r1: int, c1: int) -> str:
    first, last = f"{get_column_letter(c0)}{r0}", f"{get_column_letter(c1)}{r1}"
    return first if first == last else f"{first}:{last}"


def _is_label(span: TextSpan) -> bool:
    text = span.text.strip()
    return 0 < len(text) <= MAX_LABEL_CHARS and not text.endswith((".", ";", ",")) and is_visible(span)


def _read_region(sheet: _Sheet, region: tuple[int, int, int, int], builder: SectionBuilder) -> str | None:
    """Feed one region to the builder; return its heading if it had one."""
    r0, r1, c0, c1 = region
    name = sheet.ws.title
    filled = {r: [c for c in range(c0, c1 + 1) if (r, c) in sheet.cells] for r in range(r0, r1 + 1)}
    header_row = next((r for r in range(r0, r1 + 1) if len(filled[r]) >= 2), None)
    if header_row is not None and header_row == r1:
        header_row = None  # one multi-cell line with nothing under it is prose, not a table
    first = r0
    heading: str | None = None
    lead_end = header_row - 1 if header_row is not None else r1
    if len(filled[r0]) == 1 and (header_row is not None or r1 > r0):
        span = sheet.cells[(r0, filled[r0][0])]
        if _is_label(span):
            heading = clean_text(span.text)
            builder.heading(1, [span])
            first = r0 + 1
    prose_rows = [r for r in range(first, lead_end + 1) if filled[r]]
    if prose_rows:
        locator = f'sheet "{name}", cells {_range(prose_rows[0], c0, prose_rows[-1], c1)}'
        for index, r in enumerate(prose_rows):
            spans: list[TextSpan] = []
            for c in filled[r]:
                if spans:
                    spans.append(_CELL_JOIN)
                spans.append(sheet.cells[(r, c)])
            builder.paragraph(spans, locator=locator if index == 0 else None)
    if header_row is not None:
        columns = range(c0, c1 + 1)
        grid = [[[sheet.cells[(r, c)]] if (r, c) in sheet.cells else [] for c in columns] for r in range(header_row, r1 + 1)]
        body = list(range(header_row + 1, r1 + 1))
        builder.table(
            grid,
            row_addresses=[_range(r, c0, r, c1) for r in body],
            cell_addresses=[tuple(f"{get_column_letter(c)}{r}" for c in columns) for r in body],
            locator=f'sheet "{name}", cells {_range(header_row, c0, r1, c1)}',
        )
    return heading


def parse_xlsx(data: bytes) -> ParseResult:
    """Parse XLSX bytes into sheet regions: tables with headers and cell addresses, and prose."""
    workbook = load_workbook(io.BytesIO(data), data_only=True)
    builder = SectionBuilder()
    title: str | None = None
    try:
        for ws in workbook.worksheets:
            sheet = _Sheet(ws)
            builder.heading(0, [TextSpan(text=ws.title)])
            for region in sheet.regions():
                heading = _read_region(sheet, region, builder)
                if title is None and heading and not sheet.hidden:
                    title = heading
    finally:
        workbook.close()
    return ParseResult(title=title or workbook.properties.title or None, sections=builder.build())
