"""Conversions between stored rows and contract models."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from kestrel.contracts import (
    Chunk,
    DocumentRecord,
    DocumentStatus,
    ParsedDocument,
    SearchHit,
    SectionView,
    TableRowView,
    TableView,
    section_id,
)

HEADING_SEPARATOR = " > "

HIT_COLUMNS = (
    "c.id AS chunk_id, c.document_id, c.section_id, c.kind, c.row_index, c.text, c.heading, c.locator,"
    " s.heading_path AS section_heading_path, d.filename, d.title, d.status, d.edition"
)
"""Columns `hit_from_row` needs; select them from `chunks c`, `sections s` and `documents d`."""


def _heading_path(heading: str, section_heading_path: str | None) -> tuple[str, ...]:
    """Rebuild a chunk's heading path, preferring the section's exact list when it matches."""
    if section_heading_path:
        path = tuple(json.loads(section_heading_path))
        if HEADING_SEPARATOR.join(path) == heading:
            return path
    return tuple(heading.split(HEADING_SEPARATOR)) if heading else ()


def section_rows(document: ParsedDocument) -> list[tuple[object, ...]]:
    """Rows for the `sections` table, in ordinal order."""
    rows: list[tuple[object, ...]] = []
    for section in sorted(document.sections, key=lambda s: s.ordinal):
        table_json = None
        if section.table is not None:
            table_json = json.dumps(
                {
                    "header": list(section.table.header),
                    "rows": [{"cells": list(row.cells), "address": row.address} for row in section.table.rows],
                },
                ensure_ascii=False,
            )
        rows.append(
            (
                section_id(document.id, section.ordinal),
                document.id,
                section.ordinal,
                section.kind,
                json.dumps(list(section.heading_path), ensure_ascii=False),
                section.locator,
                section.text,
                table_json,
            )
        )
    return rows


def record_from_row(row: sqlite3.Row) -> DocumentRecord:
    """A `documents` row (with `section_count`, `finding_count`, `chunk_count` and `table_count`) as a record."""
    return DocumentRecord(
        id=row["id"],
        collection=row["collection"],
        sha256=row["sha256"],
        filename=row["filename"],
        title=row["title"],
        media_type=row["media_type"],
        family=row["family"],
        edition=row["edition"],
        effective_from=date.fromisoformat(row["effective_from"]) if row["effective_from"] else None,
        supersedes=row["supersedes"],
        status=DocumentStatus(row["status"]),
        status_reason=row["status_reason"],
        approved_by=row["approved_by"],
        ingested_at=datetime.fromisoformat(row["ingested_at"]),
        section_count=row["section_count"],
        finding_count=row["finding_count"],
        chunk_count=row["chunk_count"],
        table_count=row["table_count"],
    )


def chunk_from_row(row: sqlite3.Row) -> Chunk:
    """A `chunks` row (with `section_heading_path`) as a chunk."""
    return Chunk(
        id=row["id"],
        collection=row["collection"],
        document_id=row["document_id"],
        section_id=row["section_id"],
        kind=row["kind"],
        row_index=row["row_index"],
        text=row["text"],
        heading_path=_heading_path(row["heading"], row["section_heading_path"]),
        locator=row["locator"],
    )


def hit_from_row(row: sqlite3.Row, *, score: float, rank: int) -> SearchHit:
    """A row selected with `HIT_COLUMNS` as a search hit."""
    return SearchHit(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        section_id=row["section_id"],
        filename=row["filename"],
        title=row["title"],
        locator=row["locator"],
        heading_path=_heading_path(row["heading"], row["section_heading_path"]),
        status=DocumentStatus(row["status"]),
        edition=row["edition"],
        kind=row["kind"],
        row_index=row["row_index"],
        text=row["text"],
        score=score,
        rank=rank,
    )


def table_view_from_row(row: sqlite3.Row) -> TableView | None:
    """The table of a section row joined to its document, or None for a prose section."""
    if not row["table_json"]:
        return None
    table = json.loads(row["table_json"])
    return TableView(
        section_id=row["id"],
        document_id=row["document_id"],
        filename=row["filename"],
        title=row["title"],
        locator=row["locator"],
        status=DocumentStatus(row["status"]),
        edition=row["edition"],
        header=tuple(table["header"]),
        # Rows are numbered from 1 in stored (source) order, so a row keeps its number.
        rows=tuple(
            TableRowView(row_number=number, cells=tuple(item["cells"]), address=item.get("address"))
            for number, item in enumerate(table["rows"], start=1)
        ),
    )


def section_view_from_row(row: sqlite3.Row) -> SectionView:
    """A section row joined to its document as a section view."""
    return SectionView(
        section_id=row["id"],
        document_id=row["document_id"],
        filename=row["filename"],
        title=row["title"],
        locator=row["locator"],
        heading_path=tuple(json.loads(row["heading_path"])),
        kind=row["kind"],
        text=row["text"],
        status=DocumentStatus(row["status"]),
        edition=row["edition"],
        table=table_view_from_row(row),
    )
