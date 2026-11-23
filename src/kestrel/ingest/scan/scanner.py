"""Apply the visibility and instruction rules to a parsed document.

`scan_document` returns a copy of the document in which section text and tables hold
only what a reader could see, plus the findings: one per run of invisible text (with
an excerpt), one per instruction-like passage, and parse warnings. Sections left with
no visible text are dropped and the rest renumbered, so ordinals stay contiguous.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from kestrel.contracts import Finding, ParsedDocument, ParsedSection, ParsedTable, TableRow, TextSpan
from kestrel.ingest.scan.bidi import BidiRegion, remove_reordered
from kestrel.ingest.scan.injection import detect_instructions
from kestrel.ingest.scan.visibility import Invisibility, invisibility, primary_kind
from kestrel.ingest.text import clean_text, render_table

EXCERPT_CHARS = 300

_TAG_CHARS = range(0xE0000, 0xE0080)


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """Whitespace-collapsed text, truncated with an ellipsis."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "\u2026"


def strip_format_characters(text: str) -> tuple[str, str]:
    """Remove zero-width and other invisible format characters.

    Returns the cleaned text and any ASCII spelled by Unicode tag characters (a known
    way to smuggle invisible instructions), so the caller can report it.
    """
    smuggled = "".join(chr(ord(ch) - 0xE0000) for ch in text if 0xE0020 <= ord(ch) <= 0xE007E)
    cleaned = "".join(ch for ch in text if ord(ch) not in _TAG_CHARS and unicodedata.category(ch) != "Cf")
    return cleaned, smuggled


class _Scan:
    def __init__(self, document: ParsedDocument) -> None:
        self.document = document
        self.findings: list[Finding] = []

    def finding(self, locator: str, kind: str, severity: str, detail: str, text: str = "") -> None:
        self.findings.append(
            Finding(
                document_id=self.document.id,
                locator=locator,
                kind=kind,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                detail=detail,
                excerpt=excerpt(text),
            )
        )

    def visible(self, spans: Sequence[TextSpan], locator: str) -> tuple[list[TextSpan], str]:
        """Keep visible spans, report each run of invisible ones, return the kept spans and their text."""
        kept: list[TextSpan] = []
        run: list[TextSpan] = []
        reasons: list[Invisibility] = []
        pending_space: list[TextSpan] = []

        def close_run() -> None:
            if run:
                self.report_hidden("".join(span.text for span in run), reasons, locator)
                run.clear()
                reasons.clear()

        for span in spans:
            why = invisibility(span)
            if why:
                run.extend(pending_space)
                pending_space.clear()
                run.append(span)
                reasons.extend(r for r in why if r not in reasons)
            elif not span.text.strip() and run and "\n" not in span.text:
                pending_space.append(span)  # spaces between two invisible spans stay in the run
            else:
                close_run()
                kept.extend(pending_space)
                pending_space.clear()
                kept.append(span)
        close_run()
        kept.extend(pending_space)
        joined, regions = remove_reordered("".join(span.text for span in kept))
        for region in regions:
            self.report_bidi(region, locator)
        text, smuggled = strip_format_characters(joined)
        if smuggled.strip():
            self.report_hidden(smuggled, [Invisibility("hidden_text", "encoded in invisible Unicode tag characters")], locator)
        return kept, text

    def report_hidden(self, text: str, reasons: list[Invisibility], locator: str) -> None:
        if not text.strip():
            return
        instruction = detect_instructions(text)
        explanation = "; ".join(reason.reason for reason in reasons)
        self.finding(
            locator,
            primary_kind(reasons),
            "high" if instruction else "warning",
            f"Text a reader could not see ({explanation}) was removed from searchable content.",
            text,
        )
        if instruction:
            self.report_instruction(text, instruction.rules, locator, hidden=True)

    def report_bidi(self, region: BidiRegion, locator: str) -> None:
        """Report one bidirectional control region (see `scan.bidi` for the rule)."""
        if not region.reorders:
            self.finding(
                locator,
                "bidi_control",
                "info",
                f"A {region.description} was removed; it does not change the order of this text, which was kept.",
                region.content,
            )
            return
        instruction = detect_instructions(region.content)
        self.finding(
            locator,
            "bidi_control",
            "high" if instruction else "warning",
            f"Text inside a {region.description} is displayed in reverse order (a reader sees "
            f"\"{excerpt(region.displayed, 80)}\"), so what a reader sees differs from what is stored; "
            "it was removed from searchable content.",
            region.content,
        )
        if instruction:
            self.report_instruction(region.content, instruction.rules, locator, hidden=True)

    def report_instruction(self, text: str, rules: tuple[str, ...], locator: str, *, hidden: bool) -> None:
        where = "hidden text (removed)" if hidden else "visible text (kept; review the document)"
        self.finding(
            locator,
            "instruction_like",
            "high",
            f"Instruction-like wording aimed at AI assistants in {where}; rules: {', '.join(rules)}.",
            text,
        )

    def check_visible_text(self, text: str, locator: str) -> None:
        for line in text.split("\n"):
            match = detect_instructions(line)
            if match:
                self.report_instruction(line, match.rules, locator, hidden=False)

    def prose(self, section: ParsedSection) -> ParsedSection | None:
        kept, raw = self.visible(section.spans, section.locator)
        text = clean_text(raw)
        if not text:
            return None
        self.check_visible_text(text, section.locator)
        return section.model_copy(update={"text": text, "spans": tuple(kept)})

    def table(self, section: ParsedSection) -> ParsedSection | None:
        assert section.table is not None
        table = section.table
        rows = [table.header, *(row.cells for row in table.rows)]
        cells = _partition(section.spans, rows)
        if cells is None:
            self.finding(
                section.locator,
                "parse_warning",
                "info",
                "Table text could not be matched to its rendering; visibility was not checked for this table.",
            )
            cells = [[[TextSpan(text=cell)] if cell else [] for cell in row] for row in rows]
        new_rows: list[tuple[str, ...]] = []
        kept_spans: list[TextSpan] = []
        for index, row in enumerate(cells):
            where = f"{section.locator}, header" if index == 0 else f"{section.locator}, {_row_label(table, index)}"
            out: list[str] = []
            for cell in row:
                kept, raw = self.visible(cell, where)
                kept_spans.extend(kept)
                out.append(" ".join(clean_text(raw).split("\n")))
            new_rows.append(tuple(out))
        header, body = new_rows[0], new_rows[1:]
        rebuilt = tuple(
            TableRow(cells=cells_, address=old.address, cell_addresses=old.cell_addresses)
            for cells_, old in zip(body, table.rows, strict=True)
            if any(cells_)
        )
        if not any(header) and not rebuilt:
            return None
        text = render_table(header, [row.cells for row in rebuilt])
        self.check_visible_text(text, section.locator)
        return section.model_copy(
            update={"text": text, "spans": tuple(kept_spans), "table": ParsedTable(header=header, rows=rebuilt)}
        )


def _row_label(table: ParsedTable, index: int) -> str:
    row = table.rows[index - 1]
    return f"cells {row.address}" if row.cell_addresses else (row.address or f"row {index}")


def _partition(spans: Sequence[TextSpan], rows: Sequence[Sequence[str]]) -> list[list[list[TextSpan]]] | None:
    """Split row-major table spans back into cells, using each cell's text as the boundary."""
    queue = [span for span in spans if span.text]
    position = 0
    out: list[list[list[TextSpan]]] = []
    for row in rows:
        out_row: list[list[TextSpan]] = []
        for cell in row:
            taken: list[TextSpan] = []
            length = 0
            while length < len(cell) and position < len(queue):
                taken.append(queue[position])
                length += len(queue[position].text)
                position += 1
            if "".join(span.text for span in taken) != cell:
                return None
            out_row.append(taken)
        out.append(out_row)
    return out if position == len(queue) else None


def scan_document(document: ParsedDocument) -> tuple[ParsedDocument, list[Finding]]:
    """Remove invisible text from a parsed document and report what was found."""
    scan = _Scan(document)
    sections: list[ParsedSection] = []
    for section in document.sections:
        scanned = scan.table(section) if section.kind == "table" and section.table is not None else scan.prose(section)
        if scanned is not None:
            sections.append(scanned.model_copy(update={"ordinal": len(sections)}))
    if not sections:
        scan.finding("whole document", "parse_warning", "warning", "No visible text was found in the document.")
    return document.model_copy(update={"sections": tuple(sections)}), scan.findings
