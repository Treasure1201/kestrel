"""Text and number normalisation shared by the evidence checks.

Numbers are compared as `Decimal` values, so `R50 000`, `R50,000`, `50000` and
`R0.05 million` are the same number. Recognised forms:

- digits, with digit grouping by spaces (including no-break, narrow and thin spaces),
  commas or apostrophes: `50 000`, `50,000`, `1 000 000`;
- a decimal point, or a decimal comma followed by one or two digits (`7,5`);
- a currency prefix (`R`, `ZAR`), which is dropped;
- magnitudes: `R5 million`, `R1.5m`, `R2 billion`, `R250k`, `R10 thousand`;
- percentages: `75%`, `75 percent`, `75 per cent` (the number 75);
- ordinals and multiples: `30th`, `3x`;
- number words from one to twelve (`three months`), except idioms such as "one of";
- a minus sign written directly before the number or its currency prefix (`-R50 000`,
  `-5%`) keeps the number negative; a dash between two numbers (`50-99`, a date) is
  a range or separator, not a sign.

Numbers taken from sources are compared exactly after this normalisation
(`contains_number`); only the result of a recomputed calculation may be rounded in the
answer (`matches_calculated`: to the cent, or to the number of decimals shown).

Not numbers: citation labels (`c3`, `c7.r4`), list markers at the start of a line
(`1.`, `2)`), inline enumerations (`(1)`), and digits inside words or identifiers.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_SPACES = re.compile(r"[            　\t\r\n\f\v ]+")
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "´": "'", "`": "'"})
_INVISIBLE = re.compile(r"[​‌‍⁠﻿­]")

_LABEL = re.compile(r"\[?\bc[1-9][0-9]*(?:\.r[1-9][0-9]*)?\b\]?", re.IGNORECASE)
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*•]\s*)?\d{1,2}[.)](?=\s)")
_INLINE_ENUM = re.compile(r"\(\d{1,2}\)|(?<![\w.,])\d{1,2}\)(?=\s)")
_CURRENCY = re.compile(r"(?<![A-Za-z_])(?:ZAR|R)\s?(?=\d)")
_SIGNED_CURRENCY = re.compile(r"(?<![\w.,)])-(?:ZAR|R)\s?(?=\d)|(?<![A-Za-z_])(?:ZAR|R)\s?-(?=\d)")
_GROUP_SEP = r"[ ,']"
_NUMBER = re.compile(
    r"(?<![\w.,])(?P<sign>(?<![\w.,)])-)?(?P<whole>\d{1,3}(?:" + _GROUP_SEP + r"\d{3})+(?!\d)|\d+)"
    r"(?P<frac>\.\d+|,\d{1,2}(?!\d))?"
    r"(?:\s?(?P<mag>billion|bn|million|mil|m|thousand|k)\b)?"
    r"(?:st|nd|rd|th|x)?(?![\w])",
    re.IGNORECASE,
)
_SCALE = {"billion": Decimal(10**9), "bn": Decimal(10**9), "million": Decimal(10**6), "mil": Decimal(10**6),
          "m": Decimal(10**6), "thousand": Decimal(1000), "k": Decimal(1000)}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_WORD = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.IGNORECASE)
_ONE_IDIOM_BEFORE = re.compile(r"\b(?:no|any|every|each|which|the|this|that|only)\s*$", re.IGNORECASE)
_ONE_IDIOM_AFTER = re.compile(r"^\s*(?:of|another|or\s+more|or\s+other)\b", re.IGNORECASE)


def normalise_text(text: str) -> str:
    """Unicode-normalise, unify spaces, dashes and quotes, and collapse whitespace."""
    value = unicodedata.normalize("NFKC", text or "")
    value = _INVISIBLE.sub("", value).translate(_DASHES).translate(_QUOTES)
    return _SPACES.sub(" ", value).strip()


def strip_digit_grouping(text: str) -> str:
    """Remove grouping separators between digits and the space after a currency prefix: `R 50 000` -> `R50000`."""
    value = re.sub(r"(?<![A-Za-z_])(ZAR|R) (?=\d)", r"\1", text)
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"(?<=\d)[ ,'](?=\d{3}(?!\d))", "", value)
    return value


def comparable(text: str) -> str:
    """The form quotes and excerpts are compared in: normalised, digit grouping removed, case-folded."""
    return strip_digit_grouping(normalise_text(text)).casefold()


def _to_decimal(whole: str, frac: str | None) -> Decimal | None:
    digits = re.sub(_GROUP_SEP, "", whole)
    fraction = (frac or "").replace(",", ".")
    try:
        return Decimal(digits + fraction)
    except InvalidOperation:
        return None


def _prepare(text: str) -> str:
    # List markers are found per line, so they are removed before line breaks are collapsed.
    value = _LIST_MARKER.sub(" ", unicodedata.normalize("NFKC", text or "").replace("\r\n", "\n"))
    value = normalise_text(value)
    value = _LABEL.sub(" ", value)
    value = _INLINE_ENUM.sub(" ", value)
    value = _SIGNED_CURRENCY.sub(" -", value)
    return _CURRENCY.sub(" ", value)


def numbers_in(text: str, *, words: bool = True) -> set[Decimal]:
    """Every number written in `text`, as Decimal values (see the module docstring)."""
    prepared = _prepare(text)
    found: set[Decimal] = set()
    for match in _NUMBER.finditer(prepared):
        value = _to_decimal(match.group("whole"), match.group("frac"))
        if value is None:
            continue
        magnitude = match.group("mag")
        value = value * _SCALE[magnitude.lower()] if magnitude else value
        found.add(-value if match.group("sign") else value)
    if words:
        for match in _WORD.finditer(prepared):
            word = match.group(1).lower()
            if word == "one" and (
                _ONE_IDIOM_BEFORE.search(prepared[: match.start()]) or _ONE_IDIOM_AFTER.match(prepared[match.end():])
            ):
                continue
            found.add(Decimal(WORD_NUMBERS[word]))
    return {value.normalize() for value in found}


def percent_rates(text: str) -> set[Decimal]:
    """Percentages in `text` as fractions: `75%` gives 0.75 (used to trace calculation operands)."""
    prepared = normalise_text(text)
    rates = set()
    for match in re.finditer(r"(?<![\w.,)])(-)?(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:%|percent\b|per cent\b)", prepared, re.IGNORECASE):
        rate = Decimal(match.group(2).replace(",", ".")) / 100
        rates.add((-rate if match.group(1) else rate).normalize())
    return rates


CENT = Decimal("0.005")


def _decimals_shown(number: Decimal) -> int:
    exponent = number.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def rounds_to(value: Decimal, number: Decimal) -> bool:
    """True when `number` is `value` rounded to the cent, or to the number of decimals `number` shows."""
    if abs(value - number) <= CENT:
        return True
    places = Decimal(1).scaleb(-_decimals_shown(number))
    try:
        return value.quantize(places, rounding="ROUND_HALF_UP") == number
    except ArithmeticError:
        return False


def contains_number(values: set[Decimal], number: Decimal) -> bool:
    """True when `number` is one of `values` exactly (both already normalised; no tolerance, sign kept)."""
    return number in values


def matches_calculated(values: set[Decimal], number: Decimal) -> bool:
    """True when `number` is a recomputed calculation result, exactly or as shown rounded (see `rounds_to`)."""
    return number in values or any(rounds_to(value, number) for value in values)


def parse_number(text: str) -> Decimal | None:
    """A single number written as text, such as a calculation result `37 500.00` or `R37,500`."""
    found = numbers_in(text, words=False)
    if len(found) == 1:
        return next(iter(found))
    try:
        return Decimal(strip_digit_grouping(normalise_text(text)).lstrip("R").strip()).normalize()
    except InvalidOperation:
        return None
