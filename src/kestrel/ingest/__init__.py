"""Ingestion: parse source files, remove invisible text, decide lifecycle status, chunk and store.

Public surface:

- `ingest_collection(...)`: the whole idempotent run for one collection folder;
- `parse_file` / `parse_bytes`: one file to a `ParsedDocument` (structure, locators, spans);
- `scan_document`: visible-only copy of a document plus `Finding`s;
- `chunk_document`: searchable `Chunk`s (prose by section, tables whole and by row);
- `load_manifest` / `decide_statuses`: manifest validation and lifecycle status.
"""

from kestrel.ingest.chunker import DEFAULT_MAX_CHARS, chunk_document, number_aliases
from kestrel.ingest.files import discover_files
from kestrel.ingest.manifest import Manifest, StatusDecision, decide_statuses, load_manifest, parse_manifest
from kestrel.ingest.parsers import SUPPORTED_EXTENSIONS, media_type_for, parse_bytes, parse_file
from kestrel.ingest.pipeline import embedding_text, ingest_collection
from kestrel.ingest.scan import scan_document

__all__ = [
    "DEFAULT_MAX_CHARS",
    "SUPPORTED_EXTENSIONS",
    "Manifest",
    "StatusDecision",
    "chunk_document",
    "decide_statuses",
    "discover_files",
    "embedding_text",
    "ingest_collection",
    "load_manifest",
    "media_type_for",
    "number_aliases",
    "parse_bytes",
    "parse_file",
    "parse_manifest",
    "scan_document",
]
