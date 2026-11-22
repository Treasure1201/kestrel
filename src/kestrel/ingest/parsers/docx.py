"""DOCX parser: body in reading order, heading tree from styles, tables, run rendering.

Rendering is resolved the way a word processor does it, lowest precedence first:
document defaults, the paragraph style chain, the character style chain, then the
run's own properties. Backgrounds come from run shading or highlight, paragraph
shading, table-cell shading, table shading, then the page colour.

Headers, footers, comments and document properties are not content and are skipped.
Tracked deletions are skipped; tracked insertions are kept.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from docx import Document
from docx.oxml.ns import qn

from kestrel.contracts import TextSpan
from kestrel.ingest.parsers._builder import SPACE, ParseResult, SectionBuilder
from kestrel.ingest.scan.visibility import normalise_color

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_HEADING_NAME = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)
_HIGHLIGHT = {
    "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF", "magenta": "FF00FF", "blue": "0000FF",
    "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080", "darkGreen": "008000",
    "darkMagenta": "800080", "darkRed": "800000", "darkYellow": "808000", "darkGray": "808080",
    "lightGray": "C0C0C0", "black": "000000", "white": "FFFFFF",
}  # fmt: skip
_SKIPPED_ANCESTORS = {qn("w:del"), qn("w:moveFrom")}

Element = Any  # lxml element from python-docx's oxml layer


@dataclass(frozen=True)
class _Props:
    color: str | None = None
    size: float | None = None
    hidden: bool = False

    def over(self, other: _Props) -> _Props:
        """`self` applied on top of `other`."""
        return _Props(
            color=self.color or other.color,
            size=self.size if self.size is not None else other.size,
            hidden=self.hidden or other.hidden,
        )


def _on(element: Element | None) -> bool:
    """An OOXML toggle property: present and not switched off."""
    return element is not None and element.get(qn("w:val")) not in ("0", "false", "off")


def _fill(shd: Element | None) -> str | None:
    if shd is None:
        return None
    fill = normalise_color(shd.get(qn("w:fill")))
    if fill is None and shd.get(qn("w:val")) == "solid":
        fill = normalise_color(shd.get(qn("w:color")))
    return fill


def _rpr_props(rpr: Element | None) -> _Props:
    if rpr is None:
        return _Props()
    color_el = rpr.find(qn("w:color"))
    size_el = rpr.find(qn("w:sz"))
    size = None
    if size_el is not None and (size_el.get(qn("w:val")) or "").isdigit():
        size = int(size_el.get(qn("w:val"))) / 2
    hidden = _on(rpr.find(qn("w:vanish"))) or _on(rpr.find(qn("w:specVanish")))
    return _Props(color=normalise_color(color_el.get(qn("w:val"))) if color_el is not None else None, size=size, hidden=hidden)


class _Styles:
    """Style lookups that walk `basedOn` chains."""

    def __init__(self, styles_element: Element) -> None:
        self._styles = {s.get(qn("w:styleId")): s for s in styles_element.findall(qn("w:style"))}
        defaults = styles_element.find(qn("w:docDefaults"))
        rpr = defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}") if defaults is not None else None
        self.defaults = _rpr_props(rpr)
        self.default_paragraph = next(
            (sid for sid, s in self._styles.items() if s.get(qn("w:type")) == "paragraph" and _on_default(s)), None
        )

    def _chain(self, style_id: str | None) -> Iterator[Element]:
        seen: set[str] = set()
        while style_id and style_id in self._styles and style_id not in seen:
            seen.add(style_id)
            style = self._styles[style_id]
            yield style
            based = style.find(qn("w:basedOn"))
            style_id = based.get(qn("w:val")) if based is not None else None

    def props(self, style_id: str | None) -> _Props:
        result = _Props()
        for style in self._chain(style_id):
            result = result.over(_rpr_props(style.find(qn("w:rPr"))))
        return result

    def heading_level(self, style_id: str | None) -> int | None:
        for style in self._chain(style_id):
            name_el = style.find(qn("w:name"))
            name = (name_el.get(qn("w:val")) if name_el is not None else "") or ""
            if name.strip().lower() == "title":
                return 0
            match = _HEADING_NAME.match(name.strip())
            if match:
                return int(match.group(1))
            outline = style.find(f"{qn('w:pPr')}/{qn('w:outlineLvl')}")
            if outline is not None and (outline.get(qn("w:val")) or "").isdigit() and int(outline.get(qn("w:val"))) < 9:
                return int(outline.get(qn("w:val"))) + 1
        return None

    def numbered(self, style_id: str | None) -> bool:
        return any(style.find(f"{qn('w:pPr')}/{qn('w:numPr')}") is not None for style in self._chain(style_id))


def _on_default(style: Element) -> bool:
    return style.get(qn("w:default")) in ("1", "true", "on")


class _DocxReader:
    def __init__(self, document: Any) -> None:
        self.styles = _Styles(document.styles.element)
        background = document.element.find(qn("w:background"))
        self.page_background = normalise_color(background.get(qn("w:color"))) if background is not None else None
        self.builder = SectionBuilder()
        self.title: str | None = None
        self._counters: list[int] = []

    # paragraphs ---------------------------------------------------------------

    def _paragraph_style(self, p: Element) -> tuple[str | None, Element | None]:
        ppr = p.find(qn("w:pPr"))
        style_el = ppr.find(qn("w:pStyle")) if ppr is not None else None
        style_id = style_el.get(qn("w:val")) if style_el is not None else self.styles.default_paragraph
        return style_id, ppr

    def spans(self, p: Element, background: str | None, *, in_table: bool) -> list[TextSpan]:
        style_id, ppr = self._paragraph_style(p)
        base = self.styles.props(style_id).over(self.styles.defaults)
        para_fill = _fill(ppr.find(qn("w:shd"))) if ppr is not None else None
        background = para_fill or background or self.page_background
        out: list[TextSpan] = []
        for run in p.iter(qn("w:r")):
            if any(ancestor.tag in _SKIPPED_ANCESTORS for ancestor in run.iterancestors()):
                continue
            text = self._run_text(run, in_table=in_table)
            if not text:
                continue
            rpr = run.find(qn("w:rPr"))
            props = base
            run_background = background
            if rpr is not None:
                char_style = rpr.find(qn("w:rStyle"))
                if char_style is not None:
                    props = self.styles.props(char_style.get(qn("w:val"))).over(props)
                props = _rpr_props(rpr).over(props)
                run_background = _fill(rpr.find(qn("w:shd"))) or run_background
                highlight = rpr.find(qn("w:highlight"))
                if highlight is not None and highlight.get(qn("w:val")) in _HIGHLIGHT:
                    run_background = _HIGHLIGHT[highlight.get(qn("w:val"))]
            out.append(
                TextSpan(text=text, color=props.color, background=run_background, font_size_pt=props.size, hidden=props.hidden)
            )
        return out

    @staticmethod
    def _run_text(run: Element, *, in_table: bool) -> str:
        parts: list[str] = []
        for child in run:
            tag = child.tag
            if tag == qn("w:t"):
                parts.append(child.text or "")
            elif tag == qn("w:tab"):
                parts.append("\t")
            elif tag in (qn("w:br"), qn("w:cr")):
                parts.append(" " if in_table else "\n")
            elif tag == qn("w:noBreakHyphen"):
                parts.append("-")
        return "".join(parts)

    def paragraph(self, p: Element) -> None:
        style_id, ppr = self._paragraph_style(p)
        spans = self.spans(p, None, in_table=False)
        if not spans:
            return
        level = self.styles.heading_level(style_id)
        if ppr is not None:
            outline = ppr.find(qn("w:outlineLvl"))
            if outline is not None and (outline.get(qn("w:val")) or "").isdigit() and int(outline.get(qn("w:val"))) < 9:
                level = int(outline.get(qn("w:val"))) + 1
        auto_numbered = (ppr is not None and ppr.find(qn("w:numPr")) is not None) or self.styles.numbered(style_id)
        if level is not None:
            number = self._heading_number(level) if auto_numbered and level > 0 else None
            if self.builder.heading(level, spans, number=number):
                if self.title is None and level <= 1:
                    self.title = self.builder.heading_path[-1]
                return
            return
        if auto_numbered:
            spans = [TextSpan(text="- "), *spans]
        self.builder.paragraph(spans)

    def _heading_number(self, level: int) -> str:
        while len(self._counters) < level:
            self._counters.append(0)
        del self._counters[level:]
        self._counters[level - 1] += 1
        return ".".join(str(max(count, 1)) for count in self._counters)

    # tables -------------------------------------------------------------------

    def table(self, tbl: Element) -> None:
        tbl_pr = tbl.find(qn("w:tblPr"))
        table_fill = _fill(tbl_pr.find(qn("w:shd"))) if tbl_pr is not None else None
        grid: list[list[list[TextSpan]]] = []
        previous: list[list[TextSpan]] = []
        for tr in tbl.iter(qn("w:tr")):
            if _nearest_table(tr) is not tbl:
                continue  # rows of a nested table are read as part of their cell
            row: list[list[TextSpan]] = []
            for tc in tr.findall(qn("w:tc")):
                tc_pr = tc.find(qn("w:tcPr"))
                fill = (_fill(tc_pr.find(qn("w:shd"))) if tc_pr is not None else None) or table_fill
                cell: list[TextSpan] = []
                for p in tc.iter(qn("w:p")):
                    spans = self.spans(p, fill, in_table=True)
                    if spans:
                        if cell:
                            cell.append(SPACE)
                        cell.extend(spans)
                span_count = 1
                if tc_pr is not None:
                    merge = tc_pr.find(qn("w:vMerge"))
                    if merge is not None and merge.get(qn("w:val")) != "restart" and len(previous) > len(row):
                        cell = list(previous[len(row)])  # continuation of a vertically merged cell
                    span_el = tc_pr.find(qn("w:gridSpan"))
                    if span_el is not None and (span_el.get(qn("w:val")) or "").isdigit():
                        span_count = max(1, int(span_el.get(qn("w:val"))))
                row.extend(list(cell) for _ in range(span_count))
            grid.append(row)
            previous = row
        self.builder.table(grid)

    # body ---------------------------------------------------------------------

    def body(self, element: Element) -> None:
        for child in element.iterchildren():
            if child.tag == qn("w:p"):
                self.paragraph(child)
            elif child.tag == qn("w:tbl"):
                self.table(child)
            elif child.tag in (qn("w:sdt"), qn("w:customXml")):
                content = child.find(qn("w:sdtContent"))
                self.body(content if content is not None else child)
            elif child.tag in (qn("w:ins"), qn("w:moveTo")):
                self.body(child)


def _nearest_table(element: Element) -> Element | None:
    return next((a for a in element.iterancestors() if a.tag == qn("w:tbl")), None)


def parse_docx(data: bytes) -> ParseResult:
    """Parse DOCX bytes into sections (with locators and span rendering) and a title."""
    document = Document(io.BytesIO(data))
    reader = _DocxReader(document)
    reader.body(document.element.body)
    title = reader.title or (document.core_properties.title or None)
    return ParseResult(title=title, sections=reader.builder.build())
