"""Search scope, hits, tables and section views."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from kestrel.contracts._base import Frozen
from kestrel.contracts.documents import ChunkKind, DocumentStatus


class SearchScope(Frozen):
    """Which documents a search may rank. Applied in SQL before both rankers.

    Exactly one edition is ever in scope per family: `edition` selects it by label,
    `effective_on` selects the edition in force on that date, and with neither the
    statuses in `statuses` decide (default: `current` only). `blocked` and `pending`
    documents are never searchable, whatever is passed here.
    """

    collections: tuple[str, ...] = Field(min_length=1)
    statuses: frozenset[DocumentStatus] = frozenset({DocumentStatus.CURRENT})
    edition: str | None = None
    effective_on: date | None = None

    @model_validator(mode="after")
    def _one_selector(self) -> SearchScope:
        if self.edition is not None and self.effective_on is not None:
            raise ValueError("give an edition or an effective date, not both")
        if self.statuses & {DocumentStatus.BLOCKED, DocumentStatus.PENDING}:
            raise ValueError("blocked and pending documents are never searchable")
        return self


class ScopeDecision(Frozen):
    """The result of reading a question for edition and date references.

    Either `scope` is set (search may proceed) or `clarification` is set: a message
    asking the user to name one edition or date. A year that could be an event date or
    an edition, or two editions in one question, produce a clarification.
    """

    scope: SearchScope | None = None
    clarification: str | None = None
    explicit: bool = False
    """True when the question named an edition or date that switched the scope."""

    @model_validator(mode="after")
    def _exactly_one(self) -> ScopeDecision:
        if (self.scope is None) == (self.clarification is None):
            raise ValueError("a scope decision has exactly one of scope or clarification")
        return self


class SearchHit(Frozen):
    """One ranked chunk with everything needed to cite it."""

    chunk_id: str
    document_id: str
    section_id: str
    filename: str
    title: str
    locator: str
    heading_path: tuple[str, ...] = ()
    status: DocumentStatus
    edition: str | None = None
    kind: ChunkKind
    row_index: int | None = None
    text: str
    score: float
    """Fused score; higher is better. Comparable only within one result set."""
    rank: int = Field(ge=1)


class TableRowView(Frozen):
    """One table row as shown to the agent, labelled `<excerpt>.r<row_number>`."""

    row_number: int = Field(ge=1)
    """1-based and stable: the same row always has the same number."""
    cells: tuple[str, ...]
    address: str | None = None

    def as_text(self, header: tuple[str, ...]) -> str:
        """Render the row as `Header: value; Header: value`, the form quotes are checked against."""
        pairs = zip(header, self.cells, strict=False) if header else ((f"col{i + 1}", c) for i, c in enumerate(self.cells))
        return "; ".join(f"{name}: {value}" for name, value in pairs)


class TableView(Frozen):
    """A whole table, fetched by `get_table` for band and range lookups."""

    section_id: str
    document_id: str
    filename: str
    title: str
    locator: str
    status: DocumentStatus
    edition: str | None = None
    header: tuple[str, ...]
    rows: tuple[TableRowView, ...]


class SectionView(Frozen):
    """A whole section as returned by `AssistantService.get_section`."""

    section_id: str
    document_id: str
    filename: str
    title: str
    locator: str
    heading_path: tuple[str, ...] = ()
    kind: str
    text: str
    status: DocumentStatus
    edition: str | None = None
    table: TableView | None = None


class SearchResult(Frozen):
    """What `AssistantService.search` returns."""

    hits: tuple[SearchHit, ...] = ()
    scope: SearchScope | None = None
    clarification: str | None = None
    """Set instead of hits when the query names editions ambiguously."""
    warnings: tuple[str, ...] = ()
    """For example: the vector index is unavailable, results are keyword-only."""


class VectorItem(Frozen):
    """One embedding to store. Stamped with document and collection for pre-filtering."""

    chunk_id: str
    document_id: str
    collection: str
    embedding: tuple[float, ...]


class VectorMatch(Frozen):
    """One nearest neighbour. `distance` is cosine distance: 0 is identical."""

    chunk_id: str
    distance: float
