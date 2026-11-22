"""The gold files are well formed and every gold fact is printed in the cited source section."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from kestrel.contracts import ParsedSection

from eval.gold import DEFAULT_GOLD, REWORDED, GoldEntry, load_gold
from eval.normalise import contains_any, locator_key, normalise
from eval.workbook import question_texts

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "kestrel-products"
TEST = load_gold(DEFAULT_GOLD)
WORDED = load_gold(REWORDED)


@cache
def visible_sections() -> dict[str, tuple[ParsedSection, ...]]:
    from kestrel.ingest import parse_file, scan_document

    out = {}
    for path in sorted(SOURCE.iterdir()):
        visible, _ = scan_document(parse_file(path, "kestrel-products"))
        out[path.name] = visible.sections
    return out


def section_text(section: ParsedSection) -> str:
    if section.table is None:
        return section.text
    # Cells are joined with a separator, as the system renders rows ("Header: value; ..."),
    # so digits in neighbouring cells are never read as one grouped number.
    rows = [" | ".join(section.table.header)] + [" | ".join(r.cells) for r in section.table.rows]
    return "\n".join(rows)


def expected_text(entry: GoldEntry) -> str:
    texts = []
    for source in entry.sources:
        matches = [
            section
            for name, sections in visible_sections().items()
            if locator_key(source.document) in locator_key(name)
            for section in sections
            if locator_key(source.locator) in locator_key(section.locator)
        ]
        assert matches, f"no section matches {source}"
        texts += [section_text(s) for s in matches]
    return normalise("\n".join(texts))


def all_text() -> str:
    return normalise("\n".join(section_text(s) for sections in visible_sections().values() for s in sections))


def test_test_set_covers_q1_to_q15_and_matches_workbook() -> None:
    assert list(TEST.questions) == [f"Q{n}" for n in range(1, 16)]
    texts = question_texts()
    for qid, entry in TEST.questions.items():
        assert " ".join(texts[qid].split()) == " ".join(entry.question.split()), qid


def test_unanswerable_questions() -> None:
    assert [q for q, e in TEST.questions.items() if e.status == "not_found"] == ["Q13", "Q14"]


def test_reworded_set_shape() -> None:
    assert WORDED.question_set == "reworded"
    assert WORDED.written_before_first_run is True
    assert 8 <= len(WORDED.questions) <= 10
    entries = list(WORDED.questions.values())
    assert any(e.status == "not_found" for e in entries)
    assert any("superseded" in e.allowed_statuses for e in entries)
    assert all(int(q[1:]) > 15 for q in WORDED.questions)
    assert "WRITTEN BEFORE ANY EVALUATION RUN" in REWORDED.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("qid", "entry"),
    [(q, e) for q, e in {**TEST.questions, **WORDED.questions}.items() if e.status == "answered"],
)
def test_facts_and_conditions_are_in_the_expected_section(qid: str, entry: GoldEntry) -> None:
    text = expected_text(entry)
    requirements = list(entry.conditions) + ([] if entry.calculated else list(entry.must_include))
    for req in requirements:
        if req.label.startswith("a monthly amount") and entry.calculated:
            continue
        assert contains_any(text, req.any), f"{qid}: {req.label!r} is not in the expected section"


def test_unanswerable_topics_are_absent_from_the_documents() -> None:
    text = all_text()
    assert not re.search(r"\bdental\b", text)  # "accidental" does not count
    assert not re.search(r"\bsuicide\b", text)
    assert not re.search(r"\bper 1000\b", text)
    assert not re.search(r"\brate per\b", text)


def test_calculated_answers_follow_the_option_c_rule() -> None:
    # 100% of the first R20 000 plus 75% of the balance, capped at R300 000 (disability guide s2).
    def option_c(salary: int) -> float:
        return min(20_000 + 0.75 * max(0, salary - 20_000), 300_000) if salary > 20_000 else salary

    assert option_c(50_000) == 42_500
    assert option_c(80_000) == 65_000
    assert contains_any(normalise("R42 500"), TEST.questions["Q6"].must_include[0].any)
