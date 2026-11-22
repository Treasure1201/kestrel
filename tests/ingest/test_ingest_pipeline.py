"""The ingest pipeline: lifecycle status, idempotency, failures, removals and vectors."""

from __future__ import annotations

import shutil
from pathlib import Path

import docx
import pytest
from fakes import FakeEmbedder
from ingest.ingest_fakes import COLLECTION, MANIFEST, MemoryRepository, MemoryVectorIndex

from kestrel.contracts import DocumentStatus, IngestReport, KnowledgeConfig
from kestrel.errors import ConfigurationError
from kestrel.ingest import ingest_collection

LIFE_2025 = "KA_Group_Life_Product_Guide_2025.docx"
LIFE_2026 = "KA_Group_Life_Product_Guide_2026.docx"


@pytest.fixture
def folder(tmp_path: Path, source_documents: Path) -> Path:
    target = tmp_path / COLLECTION
    shutil.copytree(source_documents, target)
    return target


def _run(
    folder: Path,
    repository: MemoryRepository,
    vectors: MemoryVectorIndex,
    knowledge: KnowledgeConfig,
    manifest: Path | None = MANIFEST,
) -> IngestReport:
    return ingest_collection(folder, COLLECTION, knowledge, manifest, repository, vectors, FakeEmbedder())


def _actions(report: IngestReport) -> dict[str, str]:
    return {f.filename: f.action for f in report.files}


def test_first_ingest_adds_all_and_sets_edition_status(folder, repository, vectors, knowledge) -> None:
    report = _run(folder, repository, vectors, knowledge)
    assert set(_actions(report).values()) == {"added"}
    assert len(report.files) == 6
    assert report.warnings == ()
    assert repository.by_filename(LIFE_2025).status == DocumentStatus.SUPERSEDED
    assert repository.by_filename(LIFE_2026).status == DocumentStatus.CURRENT
    statuses = {r.filename: r.status for r in repository.list_documents([COLLECTION])}
    assert sum(s == DocumentStatus.CURRENT for s in statuses.values()) == 5
    life = repository.by_filename(LIFE_2026)
    assert (life.family, life.edition, life.supersedes, life.approved_by) == (
        "group-life-product-guide",
        "2026",
        "2025",
        "assessment-pack",
    )
    faq = next(f for f in report.files if f.filename == "KA_Claims_FAQ.docx")
    assert faq.findings == 2
    every_chunk = [c for chunks in repository.chunks.values() for c in chunks]
    assert set(vectors.vectors) == {c.id for c in every_chunk}


def test_second_ingest_is_a_no_op(folder, repository, vectors, knowledge) -> None:
    _run(folder, repository, vectors, knowledge)
    writes, upserts = repository.writes, vectors.upserts
    report = _run(folder, repository, vectors, knowledge)
    assert set(_actions(report).values()) == {"unchanged"}
    assert not report.changed
    assert (repository.writes, vectors.upserts) == (writes, upserts)


def test_changed_bytes_become_pending_under_required_approval(folder, repository, vectors, knowledge) -> None:
    _run(folder, repository, vectors, knowledge)
    old_id = repository.by_filename(LIFE_2026).id
    edited = docx.Document(str(folder / LIFE_2026))
    edited.add_paragraph("An extra paragraph added after approval.")
    edited.save(str(folder / LIFE_2026))

    report = _run(folder, repository, vectors, knowledge)
    assert _actions(report)[LIFE_2026] == "updated"
    record = repository.by_filename(LIFE_2026)
    assert record.status == DocumentStatus.PENDING
    assert record.id != old_id
    assert "content changed since approval" in (record.status_reason or "")
    # The superseding edition is no longer approved bytes, so the older one is current again.
    assert repository.by_filename(LIFE_2025).status == DocumentStatus.CURRENT
    assert not any(v.document_id == old_id for v in vectors.vectors.values())


def test_unlisted_file_is_pending_when_required_and_current_when_not(folder, repository, vectors, knowledge) -> None:
    (folder / "Extra_Notes.md").write_text("# Extra notes\n\nA note that nobody approved.\n", encoding="utf-8")
    _run(folder, repository, vectors, knowledge)
    assert repository.by_filename("Extra_Notes.md").status == DocumentStatus.PENDING

    open_bot = KnowledgeConfig(collections=(COLLECTION,), approval="none")
    other_repo, other_vectors = MemoryRepository(), MemoryVectorIndex()
    _run(folder, other_repo, other_vectors, open_bot, manifest=None)
    assert other_repo.by_filename("Extra_Notes.md").status == DocumentStatus.CURRENT


def test_corrupt_file_fails_without_stopping_others(folder, repository, vectors, knowledge) -> None:
    (folder / "Broken.docx").write_bytes(b"this is not a zip package")
    (folder / "Broken.pdf").write_bytes(b"%PDF-1.7\n garbage")
    report = _run(folder, repository, vectors, knowledge)
    actions = _actions(report)
    assert actions["Broken.docx"] == "failed" and actions["Broken.pdf"] == "failed"
    broken = next(f for f in report.files if f.filename == "Broken.docx")
    assert broken.detail and "not a valid Office document" in broken.detail
    assert sum(a == "added" for a in actions.values()) == 6


def test_removed_file_is_removed(folder, repository, vectors, knowledge) -> None:
    _run(folder, repository, vectors, knowledge)
    removed_id = repository.by_filename("KA_Claims_FAQ.docx").id
    (folder / "KA_Claims_FAQ.docx").unlink()
    report = _run(folder, repository, vectors, knowledge)
    assert _actions(report)["KA_Claims_FAQ.docx"] == "removed"
    assert repository.get_document(removed_id) is None
    assert not any(v.document_id == removed_id for v in vectors.vectors.values())


def test_junk_files_are_skipped(folder, repository, vectors, knowledge) -> None:
    (folder / ".DS_Store").write_bytes(b"\x00\x01")
    (folder / "~$KA_Claims_FAQ.docx").write_bytes(b"lock")
    (folder / "readme.txt").write_text("not a supported type", encoding="utf-8")
    report = _run(folder, repository, vectors, knowledge)
    names = {f.filename for f in report.files}
    assert ".DS_Store" not in names and "~$KA_Claims_FAQ.docx" not in names
    assert any("readme.txt" in w for w in report.warnings)


def test_vector_index_unavailable_keeps_keyword_index_and_backfills_later(folder, repository, knowledge) -> None:
    offline = MemoryVectorIndex(available=False)
    report = _run(folder, repository, offline, knowledge)
    assert any("Vector index unavailable" in w for w in report.warnings)
    assert len(repository.records) == 6 and all(repository.chunks.values())

    online = MemoryVectorIndex()
    second = _run(folder, repository, online, knowledge)
    assert set(_actions(second).values()) == {"unchanged"}
    assert len(online.vectors) == sum(len(c) for c in repository.chunks.values())


def test_blocked_status_is_kept(folder, repository, vectors, knowledge) -> None:
    _run(folder, repository, vectors, knowledge)
    record = repository.by_filename(LIFE_2026)
    repository.set_status(record.id, DocumentStatus.BLOCKED, reason="withdrawn for review")
    writes = repository.writes
    report = _run(folder, repository, vectors, knowledge)
    assert _actions(report)[LIFE_2026] == "unchanged"
    assert repository.by_filename(LIFE_2026).status == DocumentStatus.BLOCKED
    assert repository.writes == writes


def test_required_approval_without_manifest_is_a_configuration_error(folder, repository, vectors) -> None:
    bot = KnowledgeConfig(collections=(COLLECTION,), approval="required")
    with pytest.raises(ConfigurationError):
        _run(folder, repository, vectors, bot, manifest=None)


def test_missing_source_folder_is_a_configuration_error(tmp_path, repository, vectors, knowledge) -> None:
    with pytest.raises(ConfigurationError, match="Source folder not found"):
        _run(tmp_path / "missing", repository, vectors, knowledge)
