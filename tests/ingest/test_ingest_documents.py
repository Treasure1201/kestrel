"""The supplied documents: structure, locators, tables and the hidden instruction."""

from __future__ import annotations

from pathlib import Path

import pytest

from kestrel.contracts import ParsedDocument
from kestrel.ingest import chunk_document, parse_file, scan_document

COLLECTION = "kestrel-products"
EXPECTED = {
    "KA_Claims_FAQ.docx",
    "KA_Free_Cover_Limits_2026.xlsx",
    "KA_Funeral_Benefit_Rules_2026.docx",
    "KA_Group_Disability_Income_Guide_2026.pdf",
    "KA_Group_Life_Product_Guide_2025.docx",
    "KA_Group_Life_Product_Guide_2026.docx",
}


def _scanned(source: Path, name: str) -> tuple[ParsedDocument, list]:
    return scan_document(parse_file(source / name, COLLECTION))


@pytest.fixture(scope="module")
def corpus(source_documents: Path) -> dict[str, tuple[ParsedDocument, list]]:
    return {name: _scanned(source_documents, name) for name in sorted(EXPECTED)}


def test_all_six_documents_parse_with_locators(corpus: dict) -> None:
    assert set(corpus) == EXPECTED
    for name, (document, _) in corpus.items():
        assert document.sections, name
        assert document.title and document.title != Path(name).stem, name
        for section in document.sections:
            assert section.locator.strip(), (name, section.ordinal)
            assert section.text.strip(), (name, section.locator)
        assert [s.ordinal for s in document.sections] == list(range(len(document.sections)))
        assert chunk_document(document), name


def test_numbered_headings_give_section_locators(corpus: dict) -> None:
    document, _ = corpus["KA_Group_Life_Product_Guide_2026.docx"]
    by_locator = {s.locator: s for s in document.sections}
    section = by_locator['section 2.2 "Maximum cover"']
    assert section.heading_path[-2:] == ("2. Benefit design", "2.2 Maximum cover")
    assert "R25 000 000" in section.text


def test_hidden_instruction_removed_and_recorded(corpus: dict) -> None:
    document, findings = corpus["KA_Claims_FAQ.docx"]
    marker = "ignore all previous instructions"
    texts = [s.text for s in document.sections] + [c.text for c in chunk_document(document)]
    assert not any(marker in text.lower() for text in texts)
    assert not any("24 hours" in text for text in texts)

    hidden = [f for f in findings if f.kind in {"tiny_text", "low_contrast", "hidden_text"}]
    assert len(hidden) == 1
    assert marker in hidden[0].excerpt.lower()
    assert hidden[0].severity == "high"
    assert hidden[0].locator == 'section "How long does it take to assess a claim?"'
    assert any(f.kind == "instruction_like" and marker in f.excerpt.lower() for f in findings)

    timing = next(s for s in document.sections if "How long" in s.locator)
    assert "within 10 working days of receiving all required documents" in timing.text


def test_funeral_table_keeps_light_header_on_shaded_cells(source_documents: Path, corpus: dict) -> None:
    raw = parse_file(source_documents / "KA_Funeral_Benefit_Rules_2026.docx", COLLECTION)
    raw_table = next(s for s in raw.sections if s.kind == "table")
    header_span = next(span for span in raw_table.spans if span.text == "Insured life")
    assert header_span.color == "FFFFFF" and header_span.background == "1F4E79"

    document, findings = corpus["KA_Funeral_Benefit_Rules_2026.docx"]
    assert findings == []
    table = next(s for s in document.sections if s.kind == "table")
    assert table.locator == 'section 2 "Cover amounts", table 1'
    assert table.table is not None
    assert table.table.header == ("Insured life", "Cover amount")
    rows = {row.cells[0]: row.cells[1] for row in table.table.rows}
    assert len(rows) == 5
    assert rows["Child aged under 6"] == "R7 500"
    assert rows["Child aged 6 to 21 (or up to 25 if a full-time student)"] == "R15 000"
    row_chunks = [c for c in chunk_document(document) if c.kind == "table_row"]
    assert any("Insured life: Child aged under 6; Cover amount: R7 500" in c.text for c in row_chunks)


def test_free_cover_limits_table_has_bands_with_cell_addresses(corpus: dict) -> None:
    document, findings = corpus["KA_Free_Cover_Limits_2026.xlsx"]
    assert findings == []
    tables = [s for s in document.sections if s.kind == "table"]
    assert len(tables) == 1
    section = tables[0]
    assert section.locator == 'sheet "Free Cover Limits 2026", cells A4:E10'
    assert section.table is not None
    header = section.table.header
    assert header[0] == "Band" and "Group life free cover limit (R)" in header
    assert len(section.table.rows) == 6
    first = section.table.rows[0]
    assert first.address == "A5:E5"
    assert first.cell_addresses == ("A5", "B5", "C5", "D5", "E5")
    assert first.cells[:3] == ("1", "10", "49")
    last = section.table.rows[-1]
    assert last.cells[2] == "and above" and last.cell_addresses[0] == "A10"

    row_chunks = [c for c in chunk_document(document) if c.kind == "table_row"]
    assert len(row_chunks) == 6
    band_three = next(c for c in row_chunks if c.row_index == 3)
    assert "Members in scheme (from): 100" in band_three.text
    assert "Members in scheme (to): 249" in band_three.text
    assert "Group life free cover limit (R): R 3 500 000" in band_three.text
    assert "3500000" in band_three.text  # ungrouped form for keyword search
    assert "Disability income free cover limit (R per month): R 55 000" in band_three.text
    assert "A7:E7" in band_three.locator
    notes = next(s for s in document.sections if s.heading_path[-1] == "Notes")
    assert "fewer than 10 members" in notes.text


def test_disability_options_table_is_extracted_cell_by_cell(corpus: dict) -> None:
    document, findings = corpus["KA_Group_Disability_Income_Guide_2026.pdf"]
    assert findings == []
    table = next(s for s in document.sections if s.kind == "table")
    assert table.locator.startswith("page 1, ")
    assert table.table is not None
    assert table.table.header == (
        "Option",
        "Income replacement",
        "Waiting period",
        "Annual escalation",
        "Maximum monthly benefit",
    )
    rows = {row.cells[0]: row.cells for row in table.table.rows}
    assert set(rows) == {"A", "B", "C"}
    assert rows["A"][2] == "3 months"
    assert rows["B"][2] == "6 months"
    assert rows["C"][2] == "3 months"
    assert rows["C"][1] == "100% of the first R20 000 of monthly salary, plus 75% of the balance"
    assert rows["B"][3] == "CPI, capped at 6%"
    # Headers and footers are not content.
    assert not any("FOR ASSESSMENT PURPOSES ONLY" in s.text for s in document.sections)


def test_docx_headers_and_footers_are_skipped(corpus: dict) -> None:
    for name in ("KA_Claims_FAQ.docx", "KA_Group_Life_Product_Guide_2025.docx"):
        document, _ = corpus[name]
        assert not any("FOR ASSESSMENT PURPOSES ONLY" in s.text for s in document.sections)
