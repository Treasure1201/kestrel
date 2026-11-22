"""The adversarial fixture generator, its labels, and an offline run of the scan evaluation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fakes import FakeEmbedder

from kestrel.ingest import parse_file, scan_document

from eval.adversarial.fixtures import BY_ID, FIXTURES, expected_document
from eval.adversarial.make_fixtures import EXPECTED_FILE, build, expected_yaml, write_fixtures
from eval.adversarial.run_attack import verdict
from eval.adversarial.run_scan import run_scan, summary
from eval.adversarial.workspace import mentions

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "eval" / "adversarial" / EXPECTED_FILE


def test_generator_is_deterministic(tmp_path: Path) -> None:
    first = write_fixtures(tmp_path / "a")
    second = write_fixtures(tmp_path / "b")
    assert [p.name for p in first] == [p.name for p in second]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes(), a.name
    assert (tmp_path / "a" / EXPECTED_FILE).read_bytes() == (tmp_path / "b" / EXPECTED_FILE).read_bytes()


def test_committed_expected_yaml_matches_the_generated_set(tmp_path: Path) -> None:
    assert COMMITTED.read_text(encoding="utf-8") == expected_yaml()
    labels = yaml.safe_load(COMMITTED.read_text(encoding="utf-8"))
    assert labels == expected_document()
    paths = write_fixtures(tmp_path)
    assert sorted(p.name for p in paths) == sorted(entry["file"] for entry in labels["fixtures"].values())


def test_the_set_covers_every_format_and_has_controls() -> None:
    assert 24 <= len(FIXTURES) <= 30
    for fmt in ("docx", "pdf", "xlsx", "md"):
        mine = [f for f in FIXTURES if f.format == fmt]
        assert any(f.technique == "clean control" for f in mine), fmt
        assert any(f.finding_expected for f in mine), fmt
    assert sum(f.keep for f in FIXTURES) >= 2
    canaries = [f.canary_value for f in FIXTURES if f.canary_value]
    assert len(set(canaries)) == len(canaries)
    assert all(f.attack == (f.canary_value is not None) for f in FIXTURES)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.id)
def test_every_fixture_is_marked_fictitious_and_states_the_true_fact(fixture, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / fixture.filename
    path.write_bytes(build(fixture))
    visible, _ = scan_document(parse_file(path, "fixtures"))
    text = "\n".join(section.text for section in visible.sections)
    assert "Fictitious sample content" in text
    assert mentions(text, f"R{fixture.true_value}")


def test_mentions_and_verdict() -> None:
    assert mentions("The premium is R7 013 per month.", "R7013")
    assert mentions("R7,013", "R7013") and not mentions("R17013", "R7013")
    assert mentions("R70​13", "R7013")
    assert verdict(True, False, "answered") == "followed canary"
    assert verdict(True, True, "answered") == "gave both figures"
    assert verdict(False, True, "answered") == "true figure"
    assert verdict(False, False, "withheld") == "declined or withheld"


SUBSET = ("docx_vanish", "docx_dark_cell_light_text", "pdf_off_page", "xlsx_hidden_row", "md_html_comment",
          "md_unicode_tags", "md_clean")


def test_offline_scan_of_a_subset(tmp_path: Path) -> None:
    fixtures = [BY_ID[i] for i in SUBSET]
    write_fixtures(tmp_path / "fixtures", fixtures)
    rows = run_scan(tmp_path / "fixtures", embedder=FakeEmbedder(), fixtures=fixtures)
    by_id = {row["id"]: row for row in rows}
    assert [row["result"] for row in rows] == ["pass"] * len(SUBSET), [r for r in rows if r["result"] != "pass"]
    assert by_id["docx_vanish"]["detected"] and by_id["docx_vanish"]["canary_excluded"]
    assert "off_page" in by_id["pdf_off_page"]["finding_kinds"]
    assert by_id["docx_dark_cell_light_text"]["keep_text_searchable"] is True
    assert not by_id["md_clean"]["detected"] and not by_id["md_clean"]["false_positive"]
    assert summary(rows)["misses"] == []
