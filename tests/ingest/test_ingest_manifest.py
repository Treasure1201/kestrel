"""Manifest validation and status decisions."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from ingest.ingest_fakes import MANIFEST

from kestrel.contracts import DocumentRecord, DocumentStatus
from kestrel.errors import ConfigurationError
from kestrel.ingest import decide_statuses, load_manifest, parse_manifest

A = "a" * 64
B = "b" * 64
C = "c" * 64


def _entry(filename: str, sha: str, edition: str, **extra: object) -> dict:
    base = {
        "filename": filename,
        "sha256": sha,
        "label": filename,
        "family": "guide",
        "edition": edition,
        "effective_from": date(2026, 1, 1),
        "approved": True,
        "approved_by": "reviewer",
    }
    return {**base, **extra}


def test_shipped_manifest_matches_the_source_files(source_documents: Path) -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest.collection == "kestrel-products"
    assert len(manifest.entries) == 6
    for entry in manifest.entries:
        data = (source_documents / entry.filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry.sha256, entry.filename
        assert entry.approved and entry.approved_by == "assessment-pack"
    life_2026 = manifest.entry_for("KA_Group_Life_Product_Guide_2026.docx")
    assert life_2026 is not None and life_2026.supersedes == "2025"


def test_supersession_and_pending_rules() -> None:
    manifest = parse_manifest({"documents": [_entry("old.docx", A, "2025"), _entry("new.docx", B, "2026", supersedes="2025")]})
    files = {"old.docx": A, "new.docx": B, "loose.md": C}
    decisions = decide_statuses(files, manifest, "required")
    assert decisions["old.docx"].status == DocumentStatus.SUPERSEDED
    assert decisions["new.docx"].status == DocumentStatus.CURRENT
    assert decisions["loose.md"].status == DocumentStatus.PENDING

    changed = decide_statuses({"old.docx": A, "new.docx": C}, manifest, "required")
    assert changed["new.docx"].status == DocumentStatus.PENDING
    assert changed["old.docx"].status == DocumentStatus.CURRENT  # its successor is not approved bytes

    missing_successor = decide_statuses({"old.docx": A}, manifest, "required")
    assert missing_successor["old.docx"].status == DocumentStatus.CURRENT

    for approval in ("none", "auto"):
        assert decide_statuses(files, manifest, approval)["loose.md"].status == DocumentStatus.CURRENT


def test_unapproved_entry_is_pending_only_when_required() -> None:
    manifest = parse_manifest({"documents": [_entry("draft.docx", A, "1", approved=False, approved_by=None)]})
    assert decide_statuses({"draft.docx": A}, manifest, "required")["draft.docx"].status == DocumentStatus.PENDING
    assert decide_statuses({"draft.docx": A}, manifest, "none")["draft.docx"].status == DocumentStatus.CURRENT


def test_blocked_status_is_respected() -> None:
    manifest = parse_manifest({"documents": [_entry("doc.docx", A, "1")]})
    record = DocumentRecord(
        id="x",
        collection="c",
        sha256=A,
        filename="doc.docx",
        title="t",
        media_type="m",
        status=DocumentStatus.BLOCKED,
        status_reason="withdrawn",
        ingested_at=datetime.now(UTC),
    )
    decision = decide_statuses({"doc.docx": A}, manifest, "required", {"doc.docx": record})["doc.docx"]
    assert decision.status == DocumentStatus.BLOCKED and decision.reason == "withdrawn"


@pytest.mark.parametrize(
    ("documents", "message"),
    [
        ([_entry("a.docx", "xyz", "1")], "sha256"),
        ([_entry("a.docx", A, "1"), _entry("a.docx", B, "2")], "more than once"),
        ([_entry("a.docx", A, "1"), _entry("b.docx", A, "2")], "same sha256"),
        ([_entry("a.docx", A, "1"), _entry("b.docx", B, "1")], "both edition"),
        ([_entry("a.docx", A, "2", supersedes="1")], "not listed"),
        ([_entry("a.docx", A, "1", approved_by=None)], "approved_by"),
        ([{**_entry("a.docx", A, "1"), "colour": "red"}], "colour"),
    ],
)
def test_invalid_manifests_name_the_problem(documents: list, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_manifest({"documents": documents})


def test_manifest_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_manifest(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("documents: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid YAML"):
        load_manifest(bad)
    extra = tmp_path / "extra.yaml"
    extra.write_text("documents: []\nowner: me\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="owner"):
        load_manifest(extra)


def test_unquoted_numeric_edition_is_a_label() -> None:
    manifest = parse_manifest({"documents": [_entry("a.docx", A, 2026)]})
    assert manifest.entries[0].edition == "2026"
