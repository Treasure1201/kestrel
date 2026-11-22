"""Turn user text into a safe FTS5 MATCH expression.

User text never reaches FTS5 as syntax. It is split into the same tokens the index
tokenizer (`unicode61`) produces, every term is wrapped in double quotes (tokens hold
only letters and digits, so they cannot contain a quote), and terms are joined with
`OR`. Operators typed by the user (`AND`, `NEAR`, `*`, parentheses, column filters)
therefore become plain words or disappear. BM25 then rewards chunks matching more and
rarer terms.

Two kinds of token need care:

- Short labels such as the `B` in "Option B" match almost everything on their own.
  They are only searched as a phrase with their neighbour (`"option b"`), never alone.
- Numbers are written with and without digit grouping ("50 000", "50,000", "50000")
  and with or without a currency letter ("R50 000"). The tokenizer splits grouped
  digits into several tokens and keeps a letter prefix on the first group, so each
  number is searched in every one of those spellings.
"""

from __future__ import annotations

import re

MAX_TERMS = 48

_TOKEN = re.compile(r"[^\W_]+")
# Digits grouped in thousands by a space, comma, no-break or thin space: 50 000, 1,250,000.
_GROUPED = re.compile(r"(?<!\d)\d{1,3}(?:[ ,  ]\d{3})+(?!\d)")
# Digits, optionally after a short currency-like prefix (R50000, usd250).
_PREFIXED_NUMBER = re.compile(r"^([^\W\d_]{0,3})(\d+)$")

CURRENCY_PREFIXES: tuple[str, ...] = ("r",)
"""Currency letters the tokenizer keeps attached to an amount ("R50 000" -> `r50`, `000`).

A symbol such as `$` is punctuation and needs no entry. Each prefix listed here makes
a number in the query also match amounts written with that prefix.
"""

# FTS5 operator words typed in capitals are treated as noise, never as labels.
_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})

STOPWORDS = frozenset(
    """
    a about above after again all am an and any are as at be been before being below between both but by
    can could did do does doing down during each few for from further had has have having he her here hers
    how i if in into is it its itself just me more most my no nor not now of off on once only or other our
    out over own same she should so some such than that the their them then there these they this those
    through to too under until up very was we were what when where which while who whom why will with would
    you your near tell please much many
    """.split()
)


def _quote(term: str) -> str:
    return f'"{term}"'


def _grouped_phrase(digits: str) -> str:
    """`50000` -> `50 000`: the token sequence the tokenizer produces for a grouped number."""
    head = len(digits) % 3 or 3
    groups = [digits[:head]] + [digits[i : i + 3] for i in range(head, len(digits), 3)]
    return " ".join(groups)


def match_expression(query: str) -> str | None:
    """Return an FTS5 MATCH string for `query`, or None when it has nothing searchable."""
    terms: list[str] = []

    def add(term: str) -> None:
        if term and term not in terms:
            terms.append(term)

    def add_number(digits: str) -> None:
        # Every spelling the index may hold: 50000, "50 000", R50000, "R50 000".
        forms = [digits, _grouped_phrase(digits)] if len(digits) > 3 else [digits]
        for form in forms:
            add(form)
        for prefix in CURRENCY_PREFIXES:
            for form in forms:
                add(prefix + form)

    tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(query)]
    consumed: set[int] = set()

    # Grouped numbers first: search every spelling of the number, plus the tokens
    # exactly as typed (which keeps an unusual prefix such as "X50 000" together).
    for match in _GROUPED.finditer(query):
        digits = re.sub(r"\D", "", match.group(0))
        covering = [i for i, (_, start, end) in enumerate(tokens) if start < match.end() and end > match.start()]
        consumed.update(covering)
        add_number(digits)
        add(" ".join(tokens[i][0].casefold() for i in covering))

    for index, (raw, _, _) in enumerate(tokens):
        if index in consumed:
            continue
        word = raw.casefold()
        number = _PREFIXED_NUMBER.match(word)
        if number:
            add(word)
            add_number(number.group(2))
            continue
        if raw in _OPERATORS:
            continue
        is_label = len(word) <= 2 and (raw.isupper() or word not in STOPWORDS)
        if is_label:
            # A short label is only meaningful with its neighbour: "option b", "class ii".
            neighbour = index - 1 if index > 0 else index + 1
            if 0 <= neighbour < len(tokens):
                pair = sorted((index, neighbour))
                add(" ".join(tokens[i][0].casefold() for i in pair))
            continue
        if word in STOPWORDS:
            continue
        add(word)

    if not terms:
        # Everything was a stopword ("what is it?"): fall back to the plain words so the
        # query still runs rather than silently matching nothing.
        for raw, _, _ in tokens:
            add(raw.casefold())
    if not terms:
        return None
    return " OR ".join(_quote(term) for term in terms[:MAX_TERMS])
