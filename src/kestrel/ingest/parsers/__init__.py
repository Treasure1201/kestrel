"""Format parsers and the entry point that picks one by file content.

`parse_bytes` returns a `ParsedDocument` whose sections carry heading paths,
locators, tables and rendering spans. Visibility is not judged here (beyond keeping
invisible text out of headings); the scanner does that next.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path

from kestrel.contracts import ParsedDocument
from kestrel.errors import InvalidInputError
from kestrel.ingest.parsers import docx, markdown, pdf, xlsx
from kestrel.ingest.parsers._builder import ParseResult

MAX_FILE_BYTES = 50_000_000
"""Larger files are refused rather than parsed."""
MAX_PACKAGE_ENTRIES = 5_000
MAX_PACKAGE_EXPANDED_BYTES = 300_000_000
"""Limits on Office packages (zip files), so a crafted archive cannot exhaust memory."""

_PARSERS: dict[str, Callable[[bytes], ParseResult]] = {
    docx.MEDIA_TYPE: docx.parse_docx,
    pdf.MEDIA_TYPE: pdf.parse_pdf,
    xlsx.MEDIA_TYPE: xlsx.parse_xlsx,
    markdown.MEDIA_TYPE: markdown.parse_markdown,
}
_EXTENSIONS = {
    ".docx": docx.MEDIA_TYPE,
    ".pdf": pdf.MEDIA_TYPE,
    ".xlsx": xlsx.MEDIA_TYPE,
    ".md": markdown.MEDIA_TYPE,
    ".markdown": markdown.MEDIA_TYPE,
}
SUPPORTED_EXTENSIONS = frozenset(_EXTENSIONS)


def media_type_for(filename: str) -> str | None:
    """The media type parsed for a file name's extension, or None when unsupported."""
    return _EXTENSIONS.get(Path(filename).suffix.lower())


def _check_content(data: bytes, media_type: str) -> None:
    """Refuse content that does not match its extension or exceeds the safety limits."""
    if len(data) > MAX_FILE_BYTES:
        raise InvalidInputError(f"The file is larger than {MAX_FILE_BYTES // 1_000_000} MB.")
    if media_type == pdf.MEDIA_TYPE:
        if not data.lstrip()[:5].startswith(b"%PDF-"):
            raise InvalidInputError("The file has a .pdf extension but is not a PDF.")
        return
    if media_type in (docx.MEDIA_TYPE, xlsx.MEDIA_TYPE):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                entries = package.infolist()
                names = {entry.filename for entry in entries}
        except zipfile.BadZipFile as exc:
            raise InvalidInputError("The file is not a valid Office document (it is not a zip package).") from exc
        if len(entries) > MAX_PACKAGE_ENTRIES or sum(e.file_size for e in entries) > MAX_PACKAGE_EXPANDED_BYTES:
            raise InvalidInputError("The Office document expands beyond the size limits and was not parsed.")
        if any(entry.flag_bits & 0x1 for entry in entries):
            raise InvalidInputError("The Office document is encrypted and cannot be read.")
        main = "word/document.xml" if media_type == docx.MEDIA_TYPE else "xl/workbook.xml"
        if main not in names:
            raise InvalidInputError("The Office document is missing its main part and cannot be read.")


def parse_bytes(data: bytes, *, filename: str, collection: str, media_type: str | None = None) -> ParsedDocument:
    """Parse file content into a `ParsedDocument`.

    Raises `InvalidInputError` for an unsupported, malformed or oversized file. Other
    exceptions from the format libraries propagate; the pipeline reports them per file.
    """
    media_type = media_type or media_type_for(filename)
    if media_type is None or media_type not in _PARSERS:
        raise InvalidInputError(f"Unsupported file type: {Path(filename).suffix or 'no extension'}.")
    _check_content(data, media_type)
    result = _PARSERS[media_type](data)
    title = (result.title or "").strip() or Path(filename).stem
    return ParsedDocument(
        collection=collection,
        filename=filename,
        sha256=hashlib.sha256(data).hexdigest(),
        media_type=media_type,
        title=title,
        sections=result.sections,
    )


def parse_file(path: Path, collection: str) -> ParsedDocument:
    """Read and parse one file; the document's filename is the file's name."""
    return parse_bytes(path.read_bytes(), filename=path.name, collection=collection)


__all__ = ["SUPPORTED_EXTENSIONS", "media_type_for", "parse_bytes", "parse_file"]
