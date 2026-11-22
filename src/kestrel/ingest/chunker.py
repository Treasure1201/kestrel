"""Split a scanned document into searchable chunks.

- Prose: one chunk per section; a long section is split on paragraph boundaries
  (then sentences, then words, only if a single paragraph is too long) so no chunk
  exceeds `max_chars`. The heading path travels in `Chunk.heading_path`, which the
  keyword index searches as its own column and the pipeline prepends when embedding,
  so the chunk text itself stays a verbatim excerpt of the section.
- Tables: one `table` chunk holding every row, and one `table_row` chunk per row. Each
  row is written as `Header: value; Header: value` (exactly `TableRowView.as_text`),
  so a row chunk is meaningful on its own and quotes from it match the row view.

Numbers. Documents write amounts grouped ("R25 000 000", "1,000,000") while users
type them either way, and a keyword index splits "25 000 000" into three tokens that
never match "25000000". Rewriting the text would break verbatim quoting, so the text
is left exactly as displayed and, only when a chunk contains a number written in one
form, a final line lists the other form ("Also written as: 25000000, R25000000").
Grouped numbers gain their ungrouped form (with any currency prefix attached);
ungrouped numbers of five or more digits without a leading zero gain the grouped
form. Four-digit numbers are left alone because they are usually years.
"""

from __future__ import annotations

import re

from kestrel.contracts import Chunk, ParsedDocument, ParsedSection, TableRowView, chunk_id, section_id

DEFAULT_MAX_CHARS = 1200
ALIAS_PREFIX = "Also written as: "

# A number starts where no digit, decimal mark or "digit and separator" precedes it,
# so the tail of "0800 000 000" is never read as a number of its own.
_START = r"(?<![\d.,])(?<!\d[ \u00a0\u202f])"
_GROUPED = re.compile(_START + r"(\d{1,3}(?:[ \u00a0\u202f,]\d{3})+)(?!\d|[.,]\d|[ \u00a0\u202f]\d)")
_PLAIN = re.compile(_START + r"([1-9]\d{4,})(?!\d|[.,]\d)")
_PREFIX_CHAR = re.compile(r"[A-Za-z$\u00a3\u20ac]")
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


def _prefix(text: str, start: int) -> str:
    """A currency or unit prefix written directly against the number ("R25"), else ""."""
    begin = start
    while begin > 0 and _PREFIX_CHAR.match(text[begin - 1]):
        begin -= 1
    return text[begin:start] if 1 <= start - begin <= 3 else ""


def number_aliases(text: str) -> list[str]:
    """Other written forms of the numbers in `text`, in first-seen order, without duplicates."""
    aliases: list[str] = []
    for match in _GROUPED.finditer(text):
        digits = re.sub(r"[ \u00a0\u202f,]", "", match.group(1))
        prefix = _prefix(text, match.start())
        aliases.extend([digits, prefix + digits] if prefix else [digits])
    for match in _PLAIN.finditer(text):
        grouped = f"{int(match.group(1)):,}".replace(",", " ")
        prefix = _prefix(text, match.start())
        aliases.extend([grouped, prefix + grouped] if prefix else [grouped])
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases:
        if alias not in seen and alias not in text:
            seen.add(alias)
            out.append(alias)
    return out


def with_aliases(text: str) -> str:
    """`text` plus, when it has numbers in only one written form, a line with the other forms."""
    aliases = number_aliases(text)
    return f"{text}\n{ALIAS_PREFIX}{', '.join(aliases)}" if aliases else text


def split_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Pack paragraphs into parts of at most `max_chars`, splitting oversize paragraphs."""
    pieces: list[str] = []
    for paragraph in (p for p in text.split("\n") if p.strip()):
        pieces.extend(_split_long(paragraph, max_chars))
    parts: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = piece
    if current:
        parts.append(current)
    return parts


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    out: list[str] = []
    current = ""
    for sentence in filter(None, _SENTENCE_END.split(paragraph.strip())):
        units = [sentence] if len(sentence) <= max_chars else _by_words(sentence, max_chars)
        for unit in units:
            candidate = f"{current} {unit}" if current else unit
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    out.append(current)
                current = unit
    if current:
        out.append(current)
    return out


def _by_words(sentence: str, max_chars: int) -> list[str]:
    out: list[str] = []
    current = ""
    for word in sentence.split():
        while len(word) > max_chars:
            if current:
                out.append(current)
                current = ""
            out.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            out.append(current)
            current = word
    if current:
        out.append(current)
    return out


def _table_chunks(document: ParsedDocument, section: ParsedSection, sid: str) -> list[Chunk]:
    assert section.table is not None
    header = section.table.header
    chunks: list[Chunk] = []
    lines: list[str] = []
    for number, row in enumerate(section.table.rows, start=1):
        view = TableRowView(row_number=number, cells=row.cells, address=row.address)
        line = view.as_text(header)
        if not any(cell.strip() for cell in row.cells):
            continue
        lines.append(line)
        locator = f"{section.locator}, {_row_place(row.address, number)}"
        chunks.append(_chunk(document, section, sid, "table_row", number, with_aliases(line), locator))
    whole = "\n".join(lines) if lines else " | ".join(h for h in header if h)
    if whole.strip():
        chunks.insert(0, _chunk(document, section, sid, "table", 0, with_aliases(whole), section.locator))
    return chunks


def _row_place(address: str | None, number: int) -> str:
    if address and address[0].isalpha() and any(ch.isdigit() for ch in address) and not address.startswith("row"):
        return f"row {number} (cells {address})"
    return f"row {number}"


def _chunk(document: ParsedDocument, section: ParsedSection, sid: str, kind: str, index: int, text: str, locator: str) -> Chunk:
    return Chunk(
        id=chunk_id(sid, kind, index),  # type: ignore[arg-type]
        collection=document.collection,
        document_id=document.id,
        section_id=sid,
        kind=kind,  # type: ignore[arg-type]
        row_index=index if kind == "table_row" else None,
        text=text,
        heading_path=section.heading_path,
        locator=locator,
    )


def chunk_document(document: ParsedDocument, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Chunks for every section of a scanned document, in reading order."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    chunks: list[Chunk] = []
    for section in document.sections:
        sid = section_id(document.id, section.ordinal)
        if section.kind == "table" and section.table is not None:
            chunks.extend(_table_chunks(document, section, sid))
            continue
        for index, part in enumerate(split_text(section.text, max_chars)):
            chunks.append(_chunk(document, section, sid, "prose", index, with_aliases(part), section.locator))
    return chunks
