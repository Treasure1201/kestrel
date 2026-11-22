"""Which documents a search may rank: the single pre-ranking filter.

Every ranked result (keyword, vector, section and table lookups through the
retriever) passes through `eligible`. The rules, in order:

1. Only documents in `scope.collections` are considered. Collections always filter.
2. `blocked` and `pending` documents are never eligible, whatever the scope says.
3. Documents are grouped by family (a document without a family is its own family).
   At most one edition per family is ever eligible, except when the caller explicitly
   asks for several statuses with no selector.
4. With no selector, a document is eligible when its status is in `scope.statuses`
   (default: `current` only, so superseded editions are invisible).
5. With `scope.edition`, a family that has a document with that edition label uses
   that document *in place of* its current one. Families without that label are not
   affected and keep the status rule from 4.
6. With `scope.effective_on`, a family whose documents carry effective dates uses the
   one approved edition in force on that date: the latest `effective_from` on or
   before the date. A dated family with no edition in force yet contributes nothing.
   Families without any effective dates keep the status rule from 4.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from kestrel.contracts import DocumentStatus, SearchScope

_NEVER = frozenset({DocumentStatus.BLOCKED, DocumentStatus.PENDING})
_APPROVED = frozenset({DocumentStatus.CURRENT, DocumentStatus.SUPERSEDED})


@dataclass(frozen=True)
class Candidate:
    """The fields of a stored document that eligibility depends on."""

    id: str
    collection: str
    status: DocumentStatus
    family: str | None = None
    edition: str | None = None
    effective_from: date | None = None

    @property
    def family_key(self) -> str:
        """The family, or the document's own id when it has none."""
        return self.family if self.family else f"\x00{self.id}"


def _preference(doc: Candidate) -> tuple[bool, date, str]:
    """Tie-break inside one family: current first, then the latest effective date, then id."""
    return (doc.status == DocumentStatus.CURRENT, doc.effective_from or date.min, doc.id)


def _same_label(left: str | None, right: str) -> bool:
    return left is not None and left.strip().casefold() == right.strip().casefold()


def eligible(documents: Iterable[Candidate], scope: SearchScope) -> list[str]:
    """Return the ids of `documents` that a search under `scope` may rank, sorted."""
    families: dict[str, list[Candidate]] = defaultdict(list)
    for doc in documents:
        # Rules 1 and 2: wrong collection, blocked or pending are dropped before anything else.
        if doc.collection in scope.collections and doc.status not in _NEVER:
            families[doc.family_key].append(doc)

    chosen: list[str] = []
    for members in families.values():
        chosen.extend(_choose(members, scope))
    return sorted(chosen)


def _choose(members: list[Candidate], scope: SearchScope) -> list[str]:
    """Apply rules 4 to 6 to the documents of one family."""
    by_status = [doc.id for doc in members if doc.status in scope.statuses]

    if scope.edition is not None:
        # Rule 5: the named edition replaces the family's current one, if the family has it.
        named = [doc for doc in members if _same_label(doc.edition, scope.edition) and doc.status in _APPROVED]
        return [max(named, key=_preference).id] if named else by_status

    if scope.effective_on is not None:
        # Rule 6: the edition in force on the date, for families that carry dates at all.
        dated = [doc for doc in members if doc.effective_from is not None and doc.status in _APPROVED]
        if not dated:
            return by_status
        in_force = [doc for doc in dated if doc.effective_from is not None and doc.effective_from <= scope.effective_on]
        if not in_force:
            return []
        latest = max(doc.effective_from for doc in in_force if doc.effective_from is not None)
        return [max((doc for doc in in_force if doc.effective_from == latest), key=_preference).id]

    # Rule 4: no selector, the statuses decide.
    return by_status
