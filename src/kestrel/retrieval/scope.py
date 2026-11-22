"""Read edition and date references in a question and decide the search scope.

The rules (each covered by a test table):

- No edition or date mentioned: the default scope (current documents only).
- One edition ("the 2025 edition", "under the 2025 guide", "version 2025.1") or one
  date with a cue ("as of 2025-06-01", "as at 1 June 2025"): the scope switches to that
  edition or date and `explicit` is True.
- Two different editions or dates, or a comparison across editions: a clarification.
- A year that is clearly an amount or a count ("2025 members", "R2025") is ignored.
- Any other bare year, or a full date with no cue, could be an event date or an
  edition: a clarification.
- An edition must exist in the bot's collections. A year maps to the edition labelled
  with it; if none, or several, match, the clarification lists the editions there are.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from kestrel.contracts import DocumentRecord, DocumentStatus, KnowledgeConfig, ScopeDecision, SearchScope

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
            ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
            ("november", "nov"), ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_YEAR = r"(?:19|20)\d{2}"

# A full date in any of the accepted spellings. Named groups let `_parse_date` read it.
_DATE = (
    rf"(?:(?P<iy>{_YEAR})-(?P<im>\d{{1,2}})-(?P<id>\d{{1,2}})"
    rf"|(?P<dd>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<dm>{_MONTH}),?\s+(?P<dy>{_YEAR})"
    rf"|(?P<mm>{_MONTH})\s+(?P<md>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<my>{_YEAR}))"
)
_DATE_RE = re.compile(rf"\b{_DATE}\b", re.IGNORECASE)
_CUED_DATE_RE = re.compile(
    rf"\b(?:as\s+(?:of|at)|effective(?:\s+(?:on|from|as\s+of))?|in\s+force\s+on|applicable\s+on|valid\s+on)"
    rf"\s+(?:the\s+)?{_DATE}\b",
    re.IGNORECASE,
)

_NOUN = r"(?:edition|version|guide|release|issue|document|handbook|manual|rulebook)s?"
_EDITION_RES = (
    # "edition 2025", "version 2025.1", "release 3.2"
    re.compile(r"\b(?:edition|version)\s+(?:no\.?\s+)?(?P<label>\d[\w.\-]*\w|\d)\b", re.IGNORECASE),
    # "2025 edition", "the 2025 product guide", "2025.1 version"
    re.compile(rf"\b(?P<label>{_YEAR}(?:\.\d+)*)\s+(?:[a-z][a-z'\-]*\s+){{0,3}}?{_NOUN}\b", re.IGNORECASE),
    # "the guide from 2025", "edition of 2024"
    re.compile(rf"\b{_NOUN}\s+(?:of|from|for)\s+(?P<label>{_YEAR})\b", re.IGNORECASE),
    # "v2025.1"
    re.compile(r"\bv(?P<label>\d+(?:\.\d+)+)\b", re.IGNORECASE),
)

_BARE_YEAR_RE = re.compile(rf"(?<![\w$€£¥.,])({_YEAR})(?![\w%]|[.,]\d)")
_CURRENCY_BEFORE = re.compile(r"(?:\b(?:r|zar|usd|eur|gbp)|[$€£¥])\s?$", re.IGNORECASE)
_QUANTITY_AFTER = re.compile(
    r"^\s*(?:members?|employees?|people|persons?|lives|staff|workers?|units?|policies|claims?|items?|cases?"
    r"|rand|dollars?|euros?|pounds?|cents?|km|kg|hours?|days?|weeks?|months?|years?|times|percent|per\s+cent)\b",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"\b(?:compare[sd]?|comparing|comparison|versus|vs\.?|differ(?:s|ence|ences|ent)?|changed?|changes)\b"
    r"|\b(?:old|older|previous|earlier)\s+and\s+(?:the\s+)?(?:new|newer|current|latest)\b",
    re.IGNORECASE,
)
_PLURAL_EDITIONS_RE = re.compile(r"\b(?:editions|versions|both\s+guides)\b", re.IGNORECASE)

ASK_ONE = "Please ask about one edition or one date at a time, for example 'under the 2025 edition' or 'as of 2025-06-01'."
AMBIGUOUS_YEAR = (
    "Does {value} refer to an edition of the guide, or to when something happened? To use a particular edition, "
    "say for example 'under the {year} edition'; to use the rules in force on a date, say 'as of {year}-06-01'."
)


def _parse_date(match: re.Match[str]) -> date | None:
    groups = match.groupdict()
    try:
        if groups["iy"]:
            return date(int(groups["iy"]), int(groups["im"]), int(groups["id"]))
        if groups["dy"]:
            return date(int(groups["dy"]), _MONTHS[groups["dm"].lower().rstrip(".")], int(groups["dd"]))
        return date(int(groups["my"]), _MONTHS[groups["mm"].lower().rstrip(".")], int(groups["md"]))
    except (ValueError, KeyError, TypeError):
        return None


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in taken)


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _bare_years(text: str) -> list[str]:
    """Years left in `text` that are not obviously an amount or a count."""
    found = []
    for match in _BARE_YEAR_RE.finditer(text):
        if _CURRENCY_BEFORE.search(text[: match.start()]) or _QUANTITY_AFTER.match(text[match.end() :]):
            continue
        found.append(match.group(1))
    return found


def _editions(documents: Sequence[DocumentRecord]) -> list[str]:
    """Edition labels of approved documents (current or superseded), sorted."""
    labels = {
        doc.edition
        for doc in documents
        if doc.edition and doc.status in {DocumentStatus.CURRENT, DocumentStatus.SUPERSEDED}
    }
    return sorted(labels)


def _clarify(message: str) -> ScopeDecision:
    return ScopeDecision(clarification=message)


def resolve_scope(question: str, knowledge: KnowledgeConfig, documents: Sequence[DocumentRecord]) -> ScopeDecision:
    """Decide the scope for `question`. `documents` are the stored documents of the bot's collections."""
    default = SearchScope(collections=knowledge.collections)
    taken: list[tuple[int, int]] = []

    dates: list[date] = []
    for match in _CUED_DATE_RE.finditer(question):
        parsed = _parse_date(match)
        if parsed is None:
            return _clarify(f"'{match.group(0)}' is not a valid date. Please give the date as YYYY-MM-DD.")
        dates.append(parsed)
        taken.append(match.span())

    uncued_dates = []
    for match in _DATE_RE.finditer(question):
        if not _overlaps(match.span(), taken):
            uncued_dates.append(match.group(0))
            taken.append(match.span())

    labels: list[str] = []
    for pattern in _EDITION_RES:
        for match in pattern.finditer(question):
            # "2025 members and the guide" is a count followed by other words, not an edition.
            if _QUANTITY_AFTER.match(question[match.end("label") :]):
                continue
            if not _overlaps(match.span(), taken):
                labels.append(match.group("label"))
                taken.append(match.span())

    # Editions with word labels ("the second edition", "edition B") are found by name.
    for name in _editions(documents):
        if name[:1].isdigit():
            continue
        quoted = re.escape(name)
        pattern = rf"\b(?:edition|version)\s+[\"']?{quoted}[\"']?(?!\w)|(?<!\w){quoted}\s+(?:edition|version)\b"
        for match in re.finditer(pattern, question, re.IGNORECASE):
            if not _overlaps(match.span(), taken):
                labels.append(name)
                taken.append(match.span())

    distinct = {label.casefold() for label in labels} | {d.isoformat() for d in dates}
    if len(distinct) > 1:
        return _clarify(ASK_ONE)
    if _COMPARISON_RE.search(question) and (distinct or _PLURAL_EDITIONS_RE.search(question)):
        return _clarify("Comparing editions is not supported in one question. " + ASK_ONE)

    years = _bare_years(_mask(question, taken))
    if uncued_dates or years:
        value = uncued_dates[0] if uncued_dates else years[0]
        year_match = re.search(_YEAR, value)
        return _clarify(AMBIGUOUS_YEAR.format(value=value, year=year_match.group(0) if year_match else "2025"))

    if dates:
        return _date_scope(knowledge, documents, dates[0])
    if labels:
        return _edition_scope(knowledge, documents, labels[0])
    return ScopeDecision(scope=default, explicit=False)


def _date_scope(knowledge: KnowledgeConfig, documents: Sequence[DocumentRecord], on: date) -> ScopeDecision:
    approved = [d for d in documents if d.status in {DocumentStatus.CURRENT, DocumentStatus.SUPERSEDED}]
    dated = [d.effective_from for d in approved if d.effective_from is not None]
    if dated and min(dated) > on:
        return _clarify(
            f"No edition was in force on {on.isoformat()}; the earliest takes effect on {min(dated).isoformat()}."
        )
    return ScopeDecision(scope=SearchScope(collections=knowledge.collections, effective_on=on), explicit=True)


def _edition_scope(knowledge: KnowledgeConfig, documents: Sequence[DocumentRecord], label: str) -> ScopeDecision:
    available = _editions(documents)
    listing = ", ".join(available)
    if not available:
        return _clarify(f"The documents available here are not organised by edition, so edition {label} cannot be selected.")
    exact = [edition for edition in available if edition.casefold() == label.casefold()]
    if not exact and re.fullmatch(_YEAR, label):
        # A year names the edition labelled with it, such as 2025 for 2025.1.
        exact = [edition for edition in available if re.match(rf"{label}(?!\d)", edition)]
    if len(exact) > 1:
        return _clarify(f"Several editions match {label}: {', '.join(exact)}. Please name one.")
    if not exact:
        return _clarify(f"There is no edition {label} in these documents. Editions available: {listing}.")
    return ScopeDecision(scope=SearchScope(collections=knowledge.collections, edition=exact[0]), explicit=True)
