"""Chunker: prose splitting, table chunks and number forms."""

from __future__ import annotations

import pytest

from kestrel.contracts import ParsedDocument, ParsedSection, ParsedTable, TableRow, TableRowView, section_id
from kestrel.ingest import chunk_document, number_aliases
from kestrel.ingest.chunker import ALIAS_PREFIX, split_text

SHA = "d" * 64


def _document(*sections: ParsedSection) -> ParsedDocument:
    return ParsedDocument(
        collection="demo", filename="demo.docx", sha256=SHA, media_type="text/markdown", title="Demo", sections=sections
    )


def _prose(ordinal: int, text: str, path: tuple[str, ...] = ("Guide", "1. Scope")) -> ParsedSection:
    return ParsedSection(ordinal=ordinal, kind="prose", heading_path=path, locator=f'section "{path[-1]}"', text=text)


def test_short_section_is_one_chunk_with_heading_path() -> None:
    document = _document(_prose(0, "First paragraph.\nSecond paragraph."))
    chunks = chunk_document(document)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text == "First paragraph.\nSecond paragraph."
    assert chunk.heading_path == ("Guide", "1. Scope")
    assert chunk.id == f"{section_id(document.id, 0)}:p00"
    assert chunk.kind == "prose" and chunk.row_index is None
    assert chunk_document(document) == chunks  # deterministic


def test_long_section_splits_on_paragraph_boundaries() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 40 for i in range(10)]
    chunks = chunk_document(_document(_prose(0, "\n".join(p.strip() for p in paragraphs))), max_chars=500)
    assert len(chunks) > 1
    assert all(len(c.text) <= 500 for c in chunks)
    assert [c.id[-3:] for c in chunks] == [f"p{i:02d}" for i in range(len(chunks))]
    for chunk in chunks:
        assert chunk.text.startswith("Paragraph ")  # every part begins at a paragraph boundary


def test_oversize_paragraph_splits_on_sentences_then_words() -> None:
    sentence = "This sentence is about forty characters. "
    parts = split_text(sentence * 30, max_chars=200)
    assert all(len(p) <= 200 for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert all(len(p) <= 120 for p in split_text("x" * 1000, max_chars=120))


def test_tables_give_whole_and_per_row_chunks_with_headers() -> None:
    table = ParsedTable(
        header=("Plan", "Monthly fee"),
        rows=(
            TableRow(cells=("Basic", "R1 500"), address="A2:B2", cell_addresses=("A2", "B2")),
            TableRow(cells=("Plus", "R2 500"), address="A3:B3", cell_addresses=("A3", "B3")),
        ),
    )
    section = ParsedSection(
        ordinal=0, kind="table", heading_path=("Rates",), locator='sheet "Rates", cells A1:B3', text="x", table=table
    )
    chunks = chunk_document(_document(section))
    assert [c.kind for c in chunks] == ["table", "table_row", "table_row"]
    whole, first, second = chunks
    assert "Plan: Basic; Monthly fee: R1 500" in whole.text and "Plan: Plus" in whole.text
    expected = TableRowView(row_number=1, cells=("Basic", "R1 500")).as_text(table.header)
    assert first.text.startswith(expected)
    assert first.row_index == 1 and second.row_index == 2
    assert first.id.endswith(":r01") and whole.id.endswith(":t00")
    assert first.locator == 'sheet "Rates", cells A1:B3, row 1 (cells A2:B2)'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The maximum is R25 000 000 per member.", ["25000000", "R25000000"]),
        ("Limit 1,000,000 applies.", ["1000000"]),
        ("Limit 25000000 applies.", ["25 000 000"]),
        ("Pay $12000 now.", ["12 000", "$12 000"]),
        ("Call 0800 000 000 today.", []),
        ("Effective 2026, age 65, 10 working days.", []),
        ("Rate of 1 250.50 applies.", []),
    ],
)
def test_number_aliases(text: str, expected: list[str]) -> None:
    assert number_aliases(text) == expected


def test_alias_line_is_appended_without_changing_the_display_text() -> None:
    text = "The maximum is R25 000 000 per member."
    chunk = chunk_document(_document(_prose(0, text)))[0]
    assert chunk.text.startswith(text + "\n")
    assert chunk.text.endswith(f"{ALIAS_PREFIX}25000000, R25000000")


def test_empty_rows_are_skipped_and_max_chars_validated() -> None:
    table = ParsedTable(header=("A", "B"), rows=(TableRow(cells=("", "")), TableRow(cells=("1", "2"))))
    section = ParsedSection(ordinal=0, kind="table", locator="table", text="x", table=table)
    chunks = chunk_document(_document(section))
    assert [c.row_index for c in chunks if c.kind == "table_row"] == [2]
    with pytest.raises(ValueError):
        chunk_document(_document(section), max_chars=10)
