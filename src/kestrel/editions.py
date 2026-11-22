"""What changed between two editions: pure, deterministic text comparison, no model.

- Sections are aligned by their normalised heading path (case, punctuation, years and
  leading numbering such as `2.2` ignored) and kind; repeated headings align in order.
  Sections left over are paired by their last heading when it is unique on both sides.
- Aligned sections whose text differs (ignoring spacing) are reported as `changed`,
  with the numbers that differ. Sections found in one edition only are `added` or
  `removed`.
- Numbers are compared after normalisation: `R5 million` equals `R5 000 000`, and
  `1 year` and `1 years` are the same value. Values are paired by aligning the two
  texts word by word, so each change is shown next to the value it replaced.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from kestrel.contracts import SectionChange, SectionView, ValueChange

HEADING_SEPARATOR = " > "
CONTEXT_WORDS = 5

_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?:\.\d+)?(?!\d)")
_NUMBERING = re.compile(r"^(?:(?:section|part|chapter|clause)\s+)?[0-9]+(?:\.[0-9]+)*[.):]?\s+", re.IGNORECASE)
_UNITS = (
    r"working\s+days?|business\s+days?|calendar\s+days?|days?|weeks?|months?|years?|hours?|minutes?"
    r"|members?|times|per\s+cent|percent"
)
_VALUE = re.compile(
    r"(?<![\w.])(?:(?P<cur>R)\s?)?"
    r"(?P<num>\d{1,3}(?:[   ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<scale>million|billion|thousand|bn|m|k)\b)?"
    r"(?:\s?(?P<pct>%)|\s+(?P<unit>" + _UNITS + r")\b)?",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+", re.UNICODE)
_SCALES = {"thousand": 1000, "k": 1000, "million": 10**6, "m": 10**6, "billion": 10**9, "bn": 10**9}


@dataclass(frozen=True)
class Value:
    """A number found in text, with what makes two numbers the same value."""

    key: str
    """Comparison key: currency, amount after scaling, singular unit."""
    display: str
    """Normalised form for a reader, for example `R25 000 000` or `60 days`."""


def heading_key(heading: str) -> str:
    """A heading without case, punctuation, leading numbering or years: `2.2 Maximum cover` -> `maximum cover`.

    Years are dropped because a title often names its edition (`Guide 2025`, `Guide 2026`).
    """
    text = unicodedata.normalize("NFKC", heading).strip()
    text = _NUMBERING.sub("", text).casefold()
    text = _YEAR.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _group(digits: str) -> str:
    whole, _, fraction = digits.partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    text = sign + " ".join(groups)
    return f"{text}.{fraction}" if fraction else text


def _unit(word: str) -> tuple[str, str]:
    """(comparison form, display form) of a unit word."""
    shown = " ".join(word.lower().split())
    if shown in {"per cent", "percent"}:
        return "%", "%"
    singular = shown[:-1] if shown.endswith("s") and shown not in {"times"} else shown
    return singular, shown


def parse_value(match: re.Match[str]) -> Value | None:
    """The normalised value of one `_VALUE` match, or None when the digits do not form a number."""
    digits = re.sub(r"[   ,]", "", match.group("num"))
    try:
        amount = Decimal(digits)
    except InvalidOperation:
        return None
    scale = (match.group("scale") or "").lower()
    if scale and not (match.group("cur") or scale in {"million", "billion", "thousand"}):
        scale = ""  # a bare "5 m" is a distance, not five million
    if scale:
        amount *= _SCALES[scale]
    amount = amount.normalize()
    text = format(amount, "f")
    currency = "R" if match.group("cur") else ""
    if match.group("pct"):
        unit_key, unit_shown = "%", "%"
    elif match.group("unit"):
        unit_key, unit_shown = _unit(match.group("unit"))
    else:
        unit_key, unit_shown = "", ""
    whole = text.lstrip("-").partition(".")[0]
    shown = f"{currency}{_group(text) if currency or len(whole) > 4 else text}"  # 2025 stays 2025
    if unit_shown == "%":
        shown += "%"
    elif unit_shown:
        shown += f" {unit_shown}"
    return Value(key=f"{currency}|{text}|{unit_key}", display=shown)


@dataclass(frozen=True)
class _Token:
    key: str
    word: str = ""
    value: Value | None = None


def tokens(text: str) -> list[_Token]:
    """Words and values of `text` in order; each value (with its unit) is one token."""
    out: list[_Token] = []
    position = 0
    for match in _VALUE.finditer(text):
        out += [_Token(key=w.casefold(), word=w) for w in _WORD.findall(text[position:match.start()])]
        value = parse_value(match)
        if value is None:
            out += [_Token(key=w.casefold(), word=w) for w in _WORD.findall(match.group(0))]
        else:
            out.append(_Token(key=f"\x00{value.key}", value=value))
        position = match.end()
    out += [_Token(key=w.casefold(), word=w) for w in _WORD.findall(text[position:])]
    return out


def _context(items: Sequence[_Token], index: int) -> str:
    words = [t.word for t in items[max(0, index - CONTEXT_WORDS * 2):index] if t.value is None]
    return " ".join(words[-CONTEXT_WORDS:])


def value_changes(older: str, newer: str) -> list[ValueChange]:
    """Numbers that differ between two versions of a passage, paired by position in the aligned text.

    A value present on one side only is reported with the other side empty.
    """
    old, new = tokens(older), tokens(newer)
    matcher = SequenceMatcher(None, [t.key for t in old], [t.key for t in new], autojunk=False)
    changes: list[ValueChange] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        before = [t for t in old[i1:i2] if t.value is not None]
        after = [(j, t) for j, t in enumerate(new[j1:j2], start=j1) if t.value is not None]
        for n in range(max(len(before), len(after))):
            b = before[n].value if n < len(before) else None
            a = after[n][1].value if n < len(after) else None
            if a is not None and b is not None and a.key == b.key:
                continue
            index = after[n][0] if n < len(after) else j1
            changes.append(ValueChange(
                before=b.display if b else "", after=a.display if a else "", context=_context(new, index),
            ))
    return changes


def _body(section: SectionView) -> str:
    """The text compared for a section: its text, or its table rows when it has no text."""
    if section.text.strip() or section.table is None:
        return section.text
    return "\n".join(row.as_text(section.table.header) for row in section.table.rows)


def _flat(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


Key = tuple[tuple[str, ...], str, int]
"""(normalised heading path, kind, occurrence) of a section."""


def _keys(sections: Sequence[SectionView], depth: int | None = None) -> list[Key]:
    """Alignment keys; `depth=1` keys on the last heading only, for a second pass."""
    seen: dict[tuple[tuple[str, ...], str], int] = {}
    keys = []
    for section in sections:
        path = tuple(heading_key(h) for h in section.heading_path)
        base = (path[-depth:] if depth else path, section.kind)
        seen[base] = seen.get(base, 0) + 1
        keys.append((*base, seen[base]))
    return keys


def align(older: Sequence[SectionView], newer: Sequence[SectionView]) -> dict[int, int]:
    """Newer section index -> older section index.

    First by the whole normalised heading path; then sections still unpaired are paired
    by their last heading alone when it is unique on both sides (a renamed parent heading
    must not hide every section below it).
    """
    old_keys, new_keys = _keys(older), _keys(newer)
    old_index = {key: i for i, key in enumerate(old_keys)}
    pairs = {j: old_index[key] for j, key in enumerate(new_keys) if key in old_index}
    taken = set(pairs.values())
    old_last = [k for i, k in enumerate(_keys(older, 1)) if i not in taken]
    new_last = _keys(newer, 1)
    for j, key in enumerate(new_last):
        if j in pairs or not key[0] or new_last.count(key) != 1 or old_last.count(key) != 1:
            continue
        i = next(i for i, k in enumerate(_keys(older, 1)) if k == key and i not in taken)
        pairs[j] = i
        taken.add(i)
    return pairs


def _heading(section: SectionView) -> str:
    return HEADING_SEPARATOR.join(section.heading_path) or section.locator


def compare_sections(older: Sequence[SectionView], newer: Sequence[SectionView]) -> list[SectionChange]:
    """Changed, added and removed sections between two editions, in the newer edition's reading order.

    Removed sections follow, in the older edition's order. Only changed sections list values:
    every number of an added or removed section is new or gone.
    """
    pairs = align(older, newer)
    changes: list[SectionChange] = []
    for j, section in enumerate(newer):
        if j not in pairs:
            changes.append(SectionChange(
                change="added", heading=_heading(section), newer_locator=section.locator,
                newer_section_id=section.section_id, newer_text=_body(section),
            ))
            continue
        previous = older[pairs[j]]
        old_text, new_text = _body(previous), _body(section)
        if _flat(old_text) == _flat(new_text):
            continue
        changes.append(SectionChange(
            change="changed", heading=_heading(section), older_locator=previous.locator,
            newer_locator=section.locator, older_section_id=previous.section_id,
            newer_section_id=section.section_id, older_text=old_text, newer_text=new_text,
            values=tuple(value_changes(old_text, new_text)),
        ))
    paired = set(pairs.values())
    for i, section in enumerate(older):
        if i not in paired:
            changes.append(SectionChange(
                change="removed", heading=_heading(section), older_locator=section.locator,
                older_section_id=section.section_id, older_text=_body(section),
            ))
    return changes
