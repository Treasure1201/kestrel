"""Text and number normalisation shared by the grader and the gold-file checks.

`normalise` maps text to a canonical form in which equivalent ways of writing the same
fact compare equal:

- Unicode NFKC (non-breaking, narrow and thin spaces become plain spaces), case folded;
- curly quotes become straight quotes, dashes and hyphens become spaces;
- number words zero to ninety-nine become digits ("six months" -> "6 months");
- grouped digits are joined ("R25 000 000", "25,000,000", "25 000 000" -> "25000000");
- magnitudes are expanded ("R5 million", "R5m", "5.5 million", "R30k" -> digits);
- a currency prefix is dropped ("R 120 000" and "ZAR120000" -> "120000");
- trailing zero decimals are dropped ("42 500.00" -> "42500");
- "per cent" and "percent" become "%", and "75 %" becomes "75%";
- other punctuation becomes a space and whitespace is collapsed.

`contains` then matches a normalised variant as a phrase: it must start at a word
boundary, and when it ends in a digit it must not be followed by another digit or a
decimal part, so "R5 000 000" never matches inside "R5 000 0000", "2" never inside
"2.2", and "6 months" never inside "16 months".
A variant that ends in a letter may be followed by more letters, so "payslip" matches
"payslips" and "recover" matches "recovery". Variants written `re:<pattern>` are
regular expressions applied to the normalised text.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_MAGNITUDES = {
    "thousand": Decimal(1_000), "k": Decimal(1_000),
    "million": Decimal(1_000_000), "mil": Decimal(1_000_000), "m": Decimal(1_000_000), "mn": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000),
}

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'", "“": '"', "”": '"'})
_DASHES = re.compile(r"[‐-―−\-]")
_COMPOUND = re.compile(
    r"\b(" + "|".join(_TENS) + r")(?:\s+(" + "|".join(k for k in _UNITS if 0 < _UNITS[k] < 10) + r"))?\b"
)
_UNIT_WORD = re.compile(r"\b(" + "|".join(_UNITS) + r")\b")
_GROUPED = re.compile(r"(?<![\d.,])(\d{1,3})((?:[ ,']\d{3})+)(?![\d])")
_MAGNITUDE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(_MAGNITUDES, key=len, reverse=True)) + r")\b")
_CURRENCY = re.compile(r"(?<![\w])(?:zar|r)\s?(?=\d)")
_ZERO_DECIMALS = re.compile(r"(?<=\d)\.0+(?!\d)")
_PERCENT_WORD = re.compile(r"\s*\b(?:per\s?cent|percent)\b")
_PERCENT_SPACE = re.compile(r"(?<=\d)\s+%")
_PUNCT = re.compile(r"[^\w%.'/\s]")
_SENTENCE_DOT = re.compile(r"\.(?!\d)")
_SPACE = re.compile(r"\s+")


def _compound(match: re.Match[str]) -> str:
    value = _TENS[match.group(1)] + (_UNITS[match.group(2)] if match.group(2) else 0)
    return str(value)


def _magnitude(match: re.Match[str]) -> str:
    try:
        number = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:  # pragma: no cover - the pattern only admits digits
        return match.group(0)
    value = number * _MAGNITUDES[match.group(2)]
    return format(value.normalize(), "f")


def normalise(text: object) -> str:
    """Return the canonical form of `text` described in the module docstring."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold().translate(_QUOTES)
    value = _DASHES.sub(" ", value)
    value = _COMPOUND.sub(_compound, value)
    value = _UNIT_WORD.sub(lambda m: str(_UNITS[m.group(1)]), value)
    value = _CURRENCY.sub("", value)
    value = _GROUPED.sub(lambda m: m.group(1) + re.sub(r"[ ,']", "", m.group(2)), value)
    value = _MAGNITUDE.sub(_magnitude, value)
    value = _ZERO_DECIMALS.sub("", value)
    value = _PERCENT_WORD.sub("%", value)
    value = _PERCENT_SPACE.sub("%", value)
    value = _PUNCT.sub(" ", value)
    value = _SENTENCE_DOT.sub(" ", value)
    return _SPACE.sub(" ", value).strip()


def variant_pattern(variant: str) -> re.Pattern[str]:
    """Compile one gold variant into a pattern over normalised text."""
    if variant.startswith("re:"):
        return re.compile(variant[3:])
    phrase = normalise(variant)
    if not phrase:
        raise ValueError(f"variant {variant!r} is empty after normalisation")
    tail = r"(?!\d|[.,]\d)" if phrase[-1].isdigit() else ""
    return re.compile(r"(?<![\w.])" + re.escape(phrase) + tail)


def contains(normalised_text: str, variant: str) -> bool:
    """True when `variant` occurs in text that has already been passed through `normalise`."""
    return variant_pattern(variant).search(normalised_text) is not None


def contains_any(normalised_text: str, variants: tuple[str, ...] | list[str]) -> bool:
    """True when any of `variants` occurs in the normalised text."""
    return any(contains(normalised_text, v) for v in variants)


_CLAUSE_SPLIT = re.compile(r"(?<=[.!?])\s+|[;\n]|\bbut\b|\bhowever\b", re.IGNORECASE)


def clauses(text: str) -> list[str]:
    """Split raw text into clauses (sentences, semicolon parts, lines, around "but"), normalised."""
    return [n for n in (normalise(part) for part in _CLAUSE_SPLIT.split(text or "")) if n]


def locator_key(text: str) -> str:
    """Normalise a locator or filename for containment tests: case, quotes and spacing ignored."""
    value = unicodedata.normalize("NFKC", text or "").casefold().translate(_QUOTES)
    value = value.replace('"', "").replace("'", "")
    return _SPACE.sub(" ", value).strip()
