"""What changed between two editions of one document family.

Built by deterministic code, never by a model: sections are aligned by their
normalised heading path, and numbers are compared after normalisation.
"""

from __future__ import annotations

from typing import Literal

from kestrel.contracts._base import Frozen
from kestrel.contracts.documents import DocumentRecord


class ValueChange(Frozen):
    """One number that differs between the two editions of a section, normalised for display."""

    before: str
    """Value in the older edition, for example `R20 000 000` or `30 days`."""
    after: str
    """Value in the newer edition."""
    context: str = ""
    """A few words just before the value in the newer edition, so a reader knows what it is."""


class SectionChange(Frozen):
    """A section whose text differs between editions, or that exists in only one of them."""

    change: Literal["changed", "added", "removed"]
    """`added`: only in the newer edition. `removed`: only in the older one."""
    heading: str
    """The heading path, joined with ` > `."""
    older_locator: str | None = None
    newer_locator: str | None = None
    older_section_id: str | None = None
    newer_section_id: str | None = None
    older_text: str = ""
    newer_text: str = ""
    values: tuple[ValueChange, ...] = ()
    """Numbers that differ, in reading order. Empty when only wording changed."""


class EditionComparison(Frozen):
    """Result of `AssistantService.compare_editions`."""

    bot_id: str
    document_id: str
    """The document the comparison was asked for."""
    family: str | None = None
    editions: tuple[DocumentRecord, ...] = ()
    """Every edition of the family in the bot's collections, oldest first."""
    older: DocumentRecord | None = None
    newer: DocumentRecord | None = None
    changes: tuple[SectionChange, ...] = ()
    note: str | None = None
    """Why nothing was compared, for example when the document has no other edition."""

    @property
    def values(self) -> list[tuple[SectionChange, ValueChange]]:
        """Every changed value with the section it belongs to, in reading order."""
        return [(change, value) for change in self.changes for value in change.values]
