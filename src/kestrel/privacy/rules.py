"""Built-in identifier rules: normalisation, the validated SA ID check and the patterns.

Each rule replaces only the identifying value with a `[KIND]` placeholder and keeps
any label in front of it ("policy number [POLICY_NUMBER]"), so the question still
reads naturally. Rules run in a fixed order; later rules never see an earlier
placeholder as a value because placeholders contain no digits.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

_KEEP_CONTROLS = frozenset("\n\r\t")
_DROP_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def normalise(text: str) -> str:
    """NFKC-normalise and drop zero-width, format, control and unassigned characters (keeps newlines and tabs)."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in folded if ch in _KEEP_CONTROLS or unicodedata.category(ch) not in _DROP_CATEGORIES)


def luhn_valid(digits: str) -> bool:
    """Luhn checksum over a string of digits."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def is_valid_sa_id(digits: str) -> bool:
    """A 13-digit South African ID number: a real YYMMDD birth date, citizenship digit 0-2 and a valid Luhn check digit."""
    if len(digits) != 13 or not digits.isdigit():
        return False
    year, month, day = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    if not any(_is_date(century + year, month, day) for century in (1900, 2000)):
        return False
    if digits[10] not in "012":
        return False
    return luhn_valid(digits)


def _is_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


Replacer = Callable[[re.Match[str], set[str]], str]


@dataclass(frozen=True)
class Rule:
    """One built-in screening rule."""

    name: str
    pattern: re.Pattern[str]
    replace: Replacer

    def apply(self, text: str, matched: set[str]) -> str:
        return self.pattern.sub(lambda match: self.replace(match, matched), text)


def placeholder(kind: str) -> str:
    """`[KIND]` for a kind such as `sa_id`."""
    return f"[{re.sub(r'[^A-Z0-9]+', '_', kind.upper()).strip('_') or 'REDACTED'}]"


def _value(kind: str, group: str = "value") -> Replacer:
    """Replace one named group with the placeholder and keep the rest of the match."""

    def replace(match: re.Match[str], matched: set[str]) -> str:
        matched.add(kind)
        start, end = match.span(group)
        whole_start = match.start()
        text = match.group(0)
        return text[: start - whole_start] + placeholder(kind) + text[end - whole_start :]

    return replace


# --- email --------------------------------------------------------------------------
_EMAIL = re.compile(r"(?P<value>[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+)")

# --- 13-digit identifiers (single space or hyphen allowed between digits) ------------
_THIRTEEN = re.compile(r"(?<!\d)(?<!\d[ -])(?P<value>\d(?:[ -]?\d){12})(?![ -]?\d)")
_THOUSANDS = re.compile(r"\d{1,3}(?: \d{3})+")


def _thirteen(match: re.Match[str], matched: set[str]) -> str:
    raw = match.group("value")
    before = match.string[max(0, match.start() - 2) : match.start()]
    if _THOUSANDS.fullmatch(raw) and re.search(r"R ?$", before):
        return raw  # a grouped rand amount such as R1 000 000 000 000
    digits = re.sub(r"\D", "", raw)
    kind = "sa_id" if is_valid_sa_id(digits) else "identifier"
    matched.add(kind)
    return placeholder(kind)


# --- labelled bank account and card numbers -----------------------------------------
_NUMBER_WORD = r"(?:number|no\.?|nr\.?|num(?:ber)?|#)"
_ASSIGN = r"\s*(?:is\s+|was\s+|[:=#]\s*)?"
_BANK = re.compile(
    rf"(?i:\b(?:bank\s+)?(?:account|acc|acct)\.?\s*{_NUMBER_WORD}?{_ASSIGN})(?P<value>\d(?:[ -]?\d){{5,17}})(?![ -]?\d)"
)
_CARD = re.compile(
    rf"(?i:\b(?:credit\s+|debit\s+|bank\s+)?card\s*{_NUMBER_WORD}?{_ASSIGN})(?P<value>\d(?:[ -]?\d){{11,18}})(?![ -]?\d)"
)

# --- labelled reference numbers -------------------------------------------------------
_REF_LABELS = {
    "policy": "policy_number",
    "membership": "member_number",
    "member": "member_number",
    "claim": "claim_number",
    "employee": "employee_number",
    "staff": "employee_number",
    "passport": "passport_number",
}
_REFERENCE = re.compile(
    r"(?i:\b(?P<label>policy|membership|member|claim|employee|staff|passport)\s*"
    r"(?:number\b|no\b\.?|nr\b\.?|num\b|id\b|ref(?:erence)?\b|#)\s*(?:is\s+|[:=#]\s*)?)"
    r"(?P<value>(?=[A-Za-z0-9/-]*\d)[A-Za-z0-9][A-Za-z0-9/-]{2,31})(?![\w/-])"
)


def _reference(match: re.Match[str], matched: set[str]) -> str:
    kind = _REF_LABELS[match.group("label").lower()]
    return _value(kind)(match, matched)


# --- labelled dates of birth ----------------------------------------------------------
_MONTH = r"(?i:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[A-Za-z]*\.?"
_DATE = rf"(?:\d{{1,4}}[./ -]\d{{1,2}}[./ -]\d{{1,4}}|\d{{1,2}}\s+{_MONTH}\s+\d{{4}}|{_MONTH}\s+\d{{1,2}},?\s+\d{{4}})"
_DOB = re.compile(
    rf"(?i:\b(?:date\s+of\s+birth|birth\s*date|d\.?o\.?b\.?|born\s+on|born)\s*(?:is\s+|was\s+|[:=#]\s*)?)(?P<value>{_DATE})(?!\d)"
)

# --- phone numbers --------------------------------------------------------------------
_SEP = r"[\s().-]*"
_PHONE_SA = re.compile(rf"(?<![\w+(])(?P<value>\(?(?:\+27{_SEP}(?:\(0\){_SEP})?|0)[1-8]{_SEP}\d(?:{_SEP}\d){{7}})(?!{_SEP}\d)")
_PHONE_INTL = re.compile(rf"(?<![\w+])(?P<value>\+[1-9]\d{{0,2}}{_SEP}\d(?:{_SEP}\d){{6,13}})(?!{_SEP}\d)")

# --- labelled names -------------------------------------------------------------------
_WORD = r"[^\W\d_]+(?:[-'][^\W\d_]+)?"
_CAP_WORD = r"[^\W\d_a-z][^\W\d_]*(?:[-'][^\W\d_]+)?"
_NAME_LABEL = (
    r"(?:full|first|last|given|member|customer|client|caller|policyholder|claimant|applicant|employee|patient)\s+name"
)
_NAME_COLON = re.compile(
    rf"(?i:\b(?:{_NAME_LABEL}|name|surname)\s*[:=]\s*)(?P<value>{_WORD}(?:[ \t]+{_WORD}){{0,2}})"
)
_NAME_IS = re.compile(rf"(?i:\b(?:{_NAME_LABEL}|my\s+name|surname)\s+is\s+)(?P<value>{_CAP_WORD}(?:[ \t]+{_CAP_WORD}){{0,2}})")
_TITLE = re.compile(rf"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?[ \t]+(?P<value>{_CAP_WORD}(?:[ \t]+{_CAP_WORD})?)")

BUILTIN_RULES: tuple[Rule, ...] = (
    Rule("email", _EMAIL, _value("email")),
    Rule("card_number", _CARD, _value("card_number")),
    Rule("bank_account", _BANK, _value("bank_account")),
    Rule("thirteen_digits", _THIRTEEN, _thirteen),
    Rule("reference_number", _REFERENCE, _reference),
    Rule("date_of_birth", _DOB, _value("date_of_birth")),
    Rule("phone", _PHONE_SA, _value("phone")),
    Rule("phone_international", _PHONE_INTL, _value("phone")),
    Rule("name_labelled", _NAME_COLON, _value("name")),
    Rule("name_is", _NAME_IS, _value("name")),
    Rule("name_title", _TITLE, _value("name")),
)
"""The built-in rules, in the order they run."""
