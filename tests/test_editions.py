"""The edition diff: heading alignment, value normalisation, and the two real group life guides."""

from __future__ import annotations

from pathlib import Path

import pytest

from kestrel.contracts import DocumentStatus, SectionView, TableRowView, TableView
from kestrel.editions import compare_sections, heading_key, value_changes
from kestrel.ingest import parse_file, scan_document

REPO = Path(__file__).resolve().parents[1]
GUIDES = REPO / "data" / "source" / "kestrel-products"


def section(path: tuple[str, ...], text: str, n: int = 0, kind: str = "prose", table: TableView | None = None) -> SectionView:
    return SectionView(
        section_id=f"d:s{n:03d}", document_id="d", filename="guide.md", title="Guide", locator=f"section {n}",
        heading_path=path, kind=kind, text=text, status=DocumentStatus.CURRENT, table=table,
    )


def pairs(older: str, newer: str) -> list[tuple[str, str]]:
    return [(v.before, v.after) for v in value_changes(older, newer)]


@pytest.mark.parametrize(
    ("heading", "key"),
    [
        ("2.2 Maximum cover", "maximum cover"),
        ("4. Continuation option", "continuation option"),
        ("Section 3: Waiting periods", "waiting periods"),
        ("Benefit Guide 2026", "benefit guide"),
        ("CLAIMS  (general)", "claims general"),
    ],
)
def test_heading_key_ignores_numbering_case_punctuation_and_years(heading: str, key: str) -> None:
    assert heading_key(heading) == key


def test_values_are_paired_and_normalised() -> None:
    assert pairs("The maximum is R20 000 000 per member.", "The maximum is R25 000 000 per member.") == [("R20 000 000", "R25 000 000")]
    assert pairs("within 30 days, up to R3 000 000", "within 60 days, up to R5 000 000") == [("30 days", "60 days"), ("R3 000 000", "R5 000 000")]
    assert pairs("guaranteed for 1 year", "guaranteed for 2 years") == [("1 year", "2 years")]
    assert pairs("75% of salary", "80 per cent of salary") == [("75%", "80%")]
    assert pairs("R1,500,000 in total", "R2 000 000 in total") == [("R1 500 000", "R2 000 000")]


def test_equal_values_written_differently_are_not_changes() -> None:
    assert pairs("cover up to R5 million", "cover up to R5 000 000") == []
    assert pairs("within 12 months", "within 12  months") == []
    assert value_changes("Same text, 10 working days.", "Same text, 10 working days.") == []


def test_value_on_one_side_only_is_reported_with_the_other_side_empty() -> None:
    changes = value_changes("Cover ends at retirement.", "Cover ends at age 65 at the latest.")
    assert [(c.before, c.after) for c in changes] == [("", "65")]
    assert "age" in changes[0].context


def test_context_names_what_the_value_is() -> None:
    (change,) = value_changes("must be notified within 12 months of death", "must be notified within 6 months of death")
    assert change.context.endswith("notified within")


def test_sections_align_by_heading_and_report_added_and_removed() -> None:
    older = [
        section(("Guide 2025", "1. Overview"), "Same overview.", 0),
        section(("Guide 2025", "2. Waiting period"), "The waiting period is 3 months.", 1),
        section(("Guide 2025", "3. Old rule"), "Withdrawn later.", 2),
    ]
    newer = [
        section(("Guide 2026", "1. Overview"), "Same  overview.", 0),
        section(("Guide 2026", "2. Waiting Period"), "The waiting period is 6 months.", 1),
        section(("Guide 2026", "3. New benefit"), "Added this year.", 2),
    ]
    changes = compare_sections(older, newer)
    assert [(c.change, c.heading.split(" > ")[-1]) for c in changes] == [
        ("changed", "2. Waiting Period"), ("added", "3. New benefit"), ("removed", "3. Old rule"),
    ]
    assert [(v.before, v.after) for v in changes[0].values] == [("3 months", "6 months")]
    assert changes[0].older_locator == "section 1" and changes[0].newer_section_id == "d:s001"
    assert changes[1].values == () and changes[2].values == ()


def test_renamed_parent_heading_still_aligns_by_last_heading() -> None:
    older = [section(("Product guide", "Claims"), "Notify within 12 months.")]
    newer = [section(("Product handbook", "Claims"), "Notify within 6 months.")]
    (change,) = compare_sections(older, newer)
    assert change.change == "changed"
    assert [(v.before, v.after) for v in change.values] == [("12 months", "6 months")]


def test_repeated_headings_align_in_order_and_tables_compare_their_rows() -> None:
    def table(limit: str) -> TableView:
        return TableView(section_id="t", document_id="d", filename="f", title="t", locator="l", status=DocumentStatus.CURRENT,
                         header=("Band", "Limit"), rows=(TableRowView(row_number=1, cells=("1", limit)),))

    older = [section(("Limits",), "", 0, "table", table("R1 000 000")), section(("Limits",), "", 1, "table", table("R2 000 000"))]
    newer = [section(("Limits",), "", 0, "table", table("R1 000 000")), section(("Limits",), "", 1, "table", table("R2 500 000"))]
    (change,) = compare_sections(older, newer)
    assert change.newer_section_id == "d:s001"
    assert [(v.before, v.after) for v in change.values] == [("R2 000 000", "R2 500 000")]


def _sections(filename: str) -> list[SectionView]:
    parsed, _findings = scan_document(parse_file(GUIDES / filename, "kestrel-products"))
    return [
        SectionView(section_id=f"{parsed.id}:s{s.ordinal:03d}", document_id=parsed.id, filename=parsed.filename,
                    title=parsed.title, locator=s.locator, heading_path=s.heading_path, kind=s.kind, text=s.text,
                    status=DocumentStatus.CURRENT)
        for s in parsed.sections
    ]


def test_real_group_life_guides_report_every_changed_value() -> None:
    changes = compare_sections(
        _sections("KA_Group_Life_Product_Guide_2025.docx"), _sections("KA_Group_Life_Product_Guide_2026.docx")
    )
    found = {(c.heading.split(" > ")[-1], v.before, v.after) for c in changes for v in c.values}
    assert ("2.2 Maximum cover", "R20 000 000", "R25 000 000") in found
    assert ("4. Continuation option", "30 days", "60 days") in found
    assert ("4. Continuation option", "R3 000 000", "R5 000 000") in found
    assert ("5. Claims", "12 months", "6 months") in found
    assert ("6. Premiums", "1 year", "2 years") in found
    assert all(c.change == "changed" for c in changes)  # both editions have the same sections
