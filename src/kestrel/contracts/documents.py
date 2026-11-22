"""Documents: manifest entries, the parsed form produced at ingest, chunks and findings.

Identifiers are deterministic, so re-ingesting unchanged files produces the same ids
and changes nothing:

- document id: `document_id(collection, sha256)`, 16 hex characters;
- section id: `section_id(document_id, ordinal)`, for example `3f2a...:s004`;
- chunk id: `chunk_id(section_id, kind, index)`, for example `3f2a...:s004:r02` for row 2.

Excerpt labels (`c1`, `c7.r4`) are not identifiers: they are assigned per request by
the agent's tools and resolved through `Evidence`.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from kestrel.contracts._base import Frozen

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class DocumentStatus(StrEnum):
    """Lifecycle state of a document. Only `current` is searched by default."""

    CURRENT = "current"
    """Approved, and the edition in force."""
    SUPERSEDED = "superseded"
    """Approved, but replaced by a later edition; searched only when a question names it."""
    PENDING = "pending"
    """Not listed in the manifest, not approved, or its hash no longer matches. Never searched."""
    BLOCKED = "blocked"
    """Withdrawn by an operator. Never searched, whatever the scope."""


def document_id(collection: str, sha256: str) -> str:
    """Return the stable id of one file version in one collection."""
    return hashlib.sha256(f"{collection}\n{sha256}".encode()).hexdigest()[:16]


def section_id(document: str, ordinal: int) -> str:
    """Return the id of section `ordinal` (0-based, in reading order) of `document`."""
    return f"{document}:s{ordinal:03d}"


def chunk_id(section: str, kind: ChunkKind, index: int = 0) -> str:
    """Return the id of a chunk: `index` is the prose part or the 1-based table row number."""
    suffix = {"prose": "p", "table": "t", "table_row": "r"}[kind]
    return f"{section}:{suffix}{index:02d}"


class ManifestEntry(Frozen):
    """One approved file in a bot's `manifest.yaml`, bound to its exact content hash."""

    sha256: str = Field(pattern=SHA256_PATTERN)
    filename: str = Field(min_length=1)
    label: str = Field(min_length=1)
    """Human title used in citations."""
    family: str = Field(min_length=1)
    """Documents of one family are editions of the same guide."""
    edition: str = Field(min_length=1)
    """Edition label such as `2026`; questions may name it explicitly."""
    effective_from: date
    supersedes: str | None = None
    """Edition label, in the same family, that this document replaces."""
    approved: bool = False
    approved_by: str | None = None


class TextSpan(Frozen):
    """A run of text with the rendering needed to judge whether a reader could see it.

    Colours are `RRGGBB` upper-case hex, or None when inherited and unknown. The
    scanner resolves `background` from cell shading or the page before judging contrast.
    """

    text: str
    color: str | None = None
    background: str | None = None
    font_size_pt: float | None = None
    hidden: bool = False
    """Explicitly hidden by the format (for example a DOCX vanish run)."""
    page: int | None = None
    """1-based page for paged formats."""
    bbox: tuple[float, float, float, float] | None = None
    """(x0, y0, x1, y1) in points where the format provides positions."""
    page_size: tuple[float, float] | None = None
    """(width, height) in points, so off-page text can be detected."""


class TableRow(Frozen):
    """One body row of a parsed table."""

    cells: tuple[str, ...]
    address: str | None = None
    """Where the row is in the source, for example `A7:D7` or `row 3`."""
    cell_addresses: tuple[str, ...] | None = None
    """Per-cell address (`A7`, `B7`, ...) where the format has one."""


class ParsedTable(Frozen):
    """A table kept whole, with its header and body rows in source order."""

    header: tuple[str, ...]
    rows: tuple[TableRow, ...]


class ParsedSection(Frozen):
    """One section of a parsed document: a heading's prose, or one table.

    The parser fills `spans` and `text` with everything it found. The scanner returns
    a new section in which `text` (and `table`) hold only visible text; removed text is
    reported as a `Finding`.
    """

    ordinal: int = Field(ge=0)
    kind: Literal["prose", "table"]
    heading_path: tuple[str, ...] = ()
    """Headings from the document root down to this section."""
    locator: str = Field(min_length=1)
    """Human locator: `file > section 4.2 > heading`, `file > page 3 > table`, `file > sheet > A5:D9`."""
    text: str
    spans: tuple[TextSpan, ...] = ()
    table: ParsedTable | None = None


class ParsedDocument(Frozen):
    """A file after parsing (and, once scanned, with invisible text removed)."""

    collection: str
    filename: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str
    """MIME type, for example `application/pdf`."""
    title: str
    sections: tuple[ParsedSection, ...]

    @property
    def id(self) -> str:
        """The document id derived from collection and content hash."""
        return document_id(self.collection, self.sha256)


FindingKind = Literal[
    "hidden_text", "low_contrast", "tiny_text", "off_page", "instruction_like", "parse_warning"
]


class Finding(Frozen):
    """Something the scanner noticed in a document. Stored and shown, never searched."""

    document_id: str
    locator: str
    kind: FindingKind
    severity: Literal["info", "warning", "high"]
    detail: str
    excerpt: str = ""
    """The affected text, truncated by the scanner to a readable length."""


ChunkKind = Literal["prose", "table", "table_row"]


class Chunk(Frozen):
    """A searchable unit. Tables produce one `table` chunk plus one `table_row` chunk per row."""

    id: str
    collection: str
    document_id: str
    section_id: str
    kind: ChunkKind
    row_index: int | None = None
    """1-based row number for `table_row` chunks, else None."""
    text: str = Field(min_length=1)
    """Searchable text. A row chunk repeats the table header so it reads on its own."""
    heading_path: tuple[str, ...] = ()
    locator: str


class DocumentRecord(Frozen):
    """A document as stored, with its lifecycle state."""

    id: str
    collection: str
    sha256: str
    filename: str
    title: str
    media_type: str
    family: str | None = None
    edition: str | None = None
    effective_from: date | None = None
    supersedes: str | None = None
    status: DocumentStatus
    status_reason: str | None = None
    """Why the status was set, for example the operator's reason for a block."""
    approved_by: str | None = None
    ingested_at: datetime
    section_count: int = 0
    finding_count: int = 0
    chunk_count: int = 0
    """Searchable chunks, including one per table row."""
    table_count: int = 0
    """Sections that hold a table."""
