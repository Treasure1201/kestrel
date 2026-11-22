"""Build the adversarial fixture documents and their labels.

    uv run python -m eval.adversarial.make_fixtures --out results/adversarial/fixtures

Writes one file per fixture (DOCX, PDF, XLSX, Markdown) and `expected.yaml`. The output
is byte-for-byte deterministic: zip entry times and document dates are fixed, and PDFs
are written without a fresh file id. See `eval/adversarial/fixtures.py` for the labels.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import yaml

from eval.adversarial.fixtures import CLOSING, FIXTURES, Fixture, expected_document

FIXED_DATE = datetime(2026, 1, 1, 0, 0, 0)
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
DARK = "1F3864"
EXPECTED_FILE = "expected.yaml"


def normalise_zip(data: bytes) -> bytes:
    """Rewrite an Office zip with fixed entry times and fixed document dates."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            body = source.read(info.filename)
            if info.filename == "docProps/core.xml":
                text = body.decode("utf-8")
                text = re.sub(r"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)", r"\g<1>2026-01-01T00:00:00Z\g<3>", text)
                body = text.encode("utf-8")
            entry = zipfile.ZipInfo(info.filename, date_time=_ZIP_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o600 << 16
            target.writestr(entry, body)
    return out.getvalue()


# --- DOCX -----------------------------------------------------------------------------------------


def _shade(cell: object, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()  # type: ignore[attr-defined]
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _cell_text(cell: object, text: str, color: str | None = None, bold: bool = False) -> None:
    from docx.shared import RGBColor

    paragraph = cell.paragraphs[0]  # type: ignore[attr-defined]
    run = paragraph.add_run(text)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def build_docx(fixture: Fixture) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    props = doc.core_properties
    props.author = props.last_modified_by = "Kestrel Assistant demonstration"
    props.created = props.modified = FIXED_DATE
    props.revision = 1
    doc.add_heading(f"{fixture.plan} fact sheet", level=1)
    doc.add_paragraph(fixture.notice)
    if not fixture.keep:
        doc.add_paragraph(fixture.fact)
    canary = fixture.canary_text
    technique = fixture.id.removeprefix("docx_")
    if technique in {"white_on_white", "near_white", "tiny_font", "vanish"}:
        assert canary is not None
        run = doc.add_paragraph().add_run(canary)
        if technique == "white_on_white":
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        elif technique == "near_white":
            run.font.color.rgb = RGBColor(0xF8, 0xF8, 0xF8)
        elif technique == "tiny_font":
            run.font.size = Pt(1)
        else:
            run.font.hidden = True
    elif technique == "white_in_white_cell":
        assert canary is not None
        table = doc.add_table(rows=2, cols=2)
        _cell_text(table.cell(0, 0), "Item", bold=True)
        _cell_text(table.cell(0, 1), "Detail", bold=True)
        _cell_text(table.cell(1, 0), "Review")
        _shade(table.cell(1, 1), "FFFFFF")
        _cell_text(table.cell(1, 1), canary, color="FFFFFF")
    elif technique == "dark_cell_light_text":
        table = doc.add_table(rows=2, cols=2)
        for col, text in enumerate(("Plan", "Monthly premium per member")):
            _shade(table.cell(0, col), DARK)
            _cell_text(table.cell(0, col), text, color="FFFFFF", bold=True)
        _cell_text(table.cell(1, 0), fixture.plan)
        _shade(table.cell(1, 1), DARK)
        _cell_text(table.cell(1, 1), f"R{fixture.true_value}", color="FFFFFF")
    elif technique in {"visible_trigger", "visible_no_trigger"}:
        assert canary is not None
        doc.add_paragraph(canary)
    doc.add_paragraph(CLOSING)
    buffer = io.BytesIO()
    doc.save(buffer)
    return normalise_zip(buffer.getvalue())


# --- PDF ------------------------------------------------------------------------------------------


def build_pdf(fixture: Fixture) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": f"{fixture.plan} fact sheet", "author": "Kestrel Assistant demonstration",
                      "creator": "", "producer": "", "creationDate": "", "modDate": ""})
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 110), f"{fixture.plan} fact sheet", fontsize=18)
    page.insert_textbox(pymupdf.Rect(72, 130, 523, 190), fixture.notice, fontsize=10)
    page.insert_textbox(pymupdf.Rect(72, 200, 523, 230), fixture.fact, fontsize=10)
    canary = fixture.canary_text
    box = pymupdf.Rect(72, 240, 523, 280)
    technique = fixture.id.removeprefix("pdf_")
    if technique == "white_text":
        page.insert_textbox(box, canary, fontsize=10, color=(1, 1, 1))
    elif technique == "tiny_font":
        page.insert_textbox(box, canary, fontsize=1)
    elif technique == "off_page":
        page.insert_text((-1200, 260), canary, fontsize=10)
    elif technique == "invisible_render_mode":
        page.insert_textbox(box, canary, fontsize=10, render_mode=3)
    elif technique == "covered_by_shape":
        page.insert_textbox(box, canary, fontsize=10)
        page.draw_rect(box, color=None, fill=(1, 1, 1), overlay=True)
    page.insert_textbox(pymupdf.Rect(72, 300, 523, 330), CLOSING, fontsize=10)
    data = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    doc.close()
    return data


# --- XLSX -----------------------------------------------------------------------------------------


def build_xlsx(fixture: Fixture) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    wb.properties.creator = wb.properties.lastModifiedBy = "Kestrel Assistant demonstration"
    wb.properties.created = wb.properties.modified = FIXED_DATE
    ws = wb.active
    ws.title = "Premiums"
    ws["A1"] = f"{fixture.plan} fact sheet"
    ws["A2"] = fixture.notice
    ws["A4"], ws["B4"] = "Plan", "Monthly premium per member"
    ws["A5"], ws["B5"] = fixture.plan, f"R{fixture.true_value}"
    canary = fixture.canary_text
    technique = fixture.id.removeprefix("xlsx_")
    white = Font(color="FFFFFFFF")
    if technique == "white_font":
        ws["A8"] = canary
        ws["A8"].font = white
    elif technique == "white_in_white_cell":
        ws["A8"] = canary
        ws["A8"].font = white
        ws["A8"].fill = PatternFill("solid", fgColor="FFFFFFFF")
    elif technique == "hidden_row":
        ws["A8"] = canary
        ws.row_dimensions[8].hidden = True
    elif technique == "hidden_column":
        ws["F8"] = canary
        ws.column_dimensions["F"].hidden = True
    elif technique == "hidden_sheet":
        notes = wb.create_sheet("Notes")
        notes["A1"] = canary
        notes.sheet_state = "hidden"
    elif technique == "dark_fill_light_text":
        dark = PatternFill("solid", fgColor="FF" + DARK)
        for ref in ("A4", "B4", "B5"):
            ws[ref].fill = dark
            ws[ref].font = Font(color="FFFFFFFF", bold=ref != "B5")
    ws["A10"] = CLOSING
    buffer = io.BytesIO()
    wb.save(buffer)
    return normalise_zip(buffer.getvalue())


# --- Markdown -------------------------------------------------------------------------------------


def _tags(text: str) -> str:
    return "".join(chr(0xE0000 + ord(ch)) for ch in text if 0x20 <= ord(ch) <= 0x7E)


def build_markdown(fixture: Fixture) -> bytes:
    canary = fixture.canary_text or ""
    lines = [f"# {fixture.plan} fact sheet", "", fixture.notice, "", fixture.fact, ""]
    technique = fixture.id.removeprefix("md_")
    if technique == "html_comment":
        lines += [f"<!-- {canary} -->", ""]
    elif technique == "unicode_tags":
        lines += [CLOSING + _tags(canary), ""]
    elif technique == "zero_width_split":
        split = canary.replace("Ignore", "Ig​nore").replace("instructions", "in​struc​tions")
        split = split.replace(f"R{fixture.canary_value}", f"R{fixture.canary_value[:1]}​{fixture.canary_value[1:]}")
        lines += [split, ""]
    elif technique == "bidi_override":
        lines += ["‮" + canary + "‬", ""]
    if technique != "unicode_tags":
        lines += [CLOSING, ""]
    return "\n".join(lines).encode("utf-8")


BUILDERS = {"docx": build_docx, "pdf": build_pdf, "xlsx": build_xlsx, "md": build_markdown}


def build(fixture: Fixture) -> bytes:
    """The bytes of one fixture document."""
    return BUILDERS[fixture.format](fixture)


def expected_yaml() -> str:
    """`expected.yaml` as text."""
    header = "# Generated by `uv run python -m eval.adversarial.make_fixtures`; do not edit by hand.\n"
    return header + yaml.safe_dump(expected_document(), sort_keys=False, allow_unicode=False, width=120)


def write_fixtures(out: Path, fixtures: Sequence[Fixture] = FIXTURES) -> list[Path]:
    """Write every fixture and `expected.yaml` into `out`; return the document paths."""
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for fixture in fixtures:
        path = out / fixture.filename
        path.write_bytes(build(fixture))
        paths.append(path)
    (out / EXPECTED_FILE).write_text(expected_yaml(), encoding="utf-8", newline="\n")
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.adversarial.make_fixtures", description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="folder to write the documents and expected.yaml into")
    args = parser.parse_args(argv)
    paths = write_fixtures(args.out)
    print(f"wrote {len(paths)} fixtures and {EXPECTED_FILE} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
