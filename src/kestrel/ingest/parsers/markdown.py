"""Markdown parser: ATX headings, pipe tables and paragraphs.

HTML comments are not rendered, so their text is kept as hidden spans: the scanner
then removes it from searchable content and reports it, like hidden text in any
other format. Front matter (a leading `---` block) is metadata and is skipped.
"""

from __future__ import annotations

import re

from kestrel.contracts import TextSpan
from kestrel.ingest.parsers._builder import SPACE, ParseResult, SectionBuilder

MEDIA_TYPE = "text/markdown"

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]


def _spans(text: str) -> list[TextSpan]:
    """Split text into visible spans and hidden spans for HTML comments."""
    out: list[TextSpan] = []
    position = 0
    for match in _COMMENT.finditer(text):
        if match.start() > position:
            out.append(TextSpan(text=text[position : match.start()]))
        if match.group(1).strip():
            out.append(TextSpan(text=match.group(1).strip(), hidden=True))
        position = match.end()
    if position < len(text):
        out.append(TextSpan(text=text[position:]))
    return out


def parse_markdown(data: bytes) -> ParseResult:
    """Parse UTF-8 Markdown bytes into sections."""
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() in ("---", "...")), None)
        if end is not None:
            lines = lines[end + 1 :]
    # A comment that spans lines is joined onto one logical line so it stays one hidden span.
    joined = "\n".join(lines)
    joined = _COMMENT.sub(lambda m: m.group(0).replace("\n", " "), joined)
    lines = joined.split("\n")

    builder = SectionBuilder()
    title: str | None = None
    paragraph: list[str] = []
    in_fence = False
    index = 0

    def flush() -> None:
        if paragraph:
            spans: list[TextSpan] = []
            for line in paragraph:
                if spans:
                    spans.append(SPACE)
                spans.extend(_spans(line))
            builder.paragraph(spans)
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence:
            builder.paragraph(_spans(line) or [TextSpan(text=" ")])
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            if builder.heading(level, _spans(heading.group(2))) and title is None and level == 1:
                title = builder.heading_path[-1]
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and _TABLE_RULE.match(lines[index + 1]):
            flush()
            rows = [_cells(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_cells(lines[index]))
                index += 1
            builder.table([[_spans(cell) for cell in row] for row in rows])
            continue
        if not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return ParseResult(title=title, sections=builder.build())
