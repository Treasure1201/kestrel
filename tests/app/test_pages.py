"""AppTest smoke tests: every page renders with a fake service, and each answer outcome shows correctly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app_fakes import BOT, FakeService, answered, audit, citation, document, finding, not_found, withheld
from streamlit.testing.v1 import AppTest

from kestrel.config import Settings
from kestrel.contracts import DocumentStatus, Outcome
from kestrel.errors import ConfigurationError, NotIngestedError

from kestrel_app import runtime

APP = Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py"
PAGES = ("views/ask.py", "views/documents.py", "views/quality.py")


def start(fake: FakeService | None, tmp_path: Path, roles: tuple[str, ...] = ("staff", "broker"), page: str | None = None) -> AppTest:
    settings = Settings(_env_file=None, roles=roles, default_bot=BOT.id)
    runtime.use_service((lambda _s: fake) if fake is not None else _broken, settings=settings, results_root=tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    if page is not None:
        at.switch_page(page).run()
    return at


def _broken(_settings: Settings):
    raise ConfigurationError("The service is not configured; run kestrel doctor.")


def text(at: AppTest) -> str:
    """Everything a reader would see as text, joined."""
    parts: list[str] = []
    for kind in ("markdown", "caption", "info", "warning", "error", "success", "title", "subheader"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [str(e.value) for e in at.get("badge")]
    parts += [str(e.label) for e in at.button]
    parts += [str(e.label) for e in at.expander]
    parts += [str(e.label) for e in at.tabs]
    return "\n".join(parts)


@pytest.mark.parametrize("page", PAGES)
def test_each_page_starts_without_exceptions(page: str, tmp_path: Path) -> None:
    fake = FakeService(documents=[document()], findings=[finding()], audit_records=[audit(1)])
    at = start(fake, tmp_path, page=page)
    assert not at.exception, at.exception
    assert "Demo Assistant" in text(at) or page != "views/ask.py"


def test_answered_question_renders_card_with_citation(tmp_path: Path) -> None:
    fake = FakeService(ask_result=answered())
    at = start(fake, tmp_path)
    at.chat_input[0].set_value("What is the waiting period? ID 8001015009087").run()
    assert not at.exception, at.exception
    shown = text(at)
    assert fake.asked and fake.asked[0][0] == BOT.id
    assert "Answered" in shown
    assert "Evidence checks passed" in shown
    assert "Verified" not in shown
    assert "The waiting period is 3 months." in shown
    assert "c1 · guide.docx" in shown  # citation chip
    assert "the waiting period is 3 months" in shown  # verbatim quote in the evidence panel
    assert "1 item of personal information (sa_id) was removed" in shown
    assert "c2 · Guide" in shown  # retrieved, not cited
    assert "US$0.0042" in shown


def test_whole_section_opens_from_the_evidence_panel(tmp_path: Path) -> None:
    at = start(FakeService(ask_result=answered()), tmp_path)
    at.chat_input[0].set_value("What is the waiting period?").run()
    next(b for b in at.button if b.label == "Open section").click().run()
    assert not at.exception, at.exception
    assert "Whole section text." in text(at)


def test_history_is_kept_per_bot(tmp_path: Path) -> None:
    other = BOT.model_copy(update={"id": "other-bot", "name": "Other Assistant"})
    at = start(FakeService(bots=(BOT, other), ask_result=answered()), tmp_path)
    at.chat_input[0].set_value("What is the waiting period?").run()
    at.selectbox(key="bot_picker").select("other-bot").run()
    assert not at.exception, at.exception
    assert "The waiting period is 3 months." not in text(at)
    at.selectbox(key="bot_picker").select(BOT.id).run()
    assert "The waiting period is 3 months." in text(at)


def test_flag_writes_local_file(tmp_path: Path) -> None:
    at = start(FakeService(ask_result=answered()), tmp_path)
    at.chat_input[0].set_value("What is the waiting period?").run()
    next(b for b in at.button if b.label == "Flag this answer").click().run()
    assert not at.exception, at.exception
    lines = (tmp_path / "flags" / "flags.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["trace_id"] == "trace-answered"
    assert any(b.label == "Flagged for review" for b in at.button)


def test_not_found_renders_decline_with_reason(tmp_path: Path) -> None:
    at = start(FakeService(ask_result=not_found()), tmp_path)
    at.chat_input[0].set_value("Is dental cover offered?").run()
    assert not at.exception, at.exception
    shown = text(at)
    assert "Not in the documents" in shown
    assert "do not mention dental cover" in shown
    assert "Report a content gap" in shown


def test_withheld_shows_failed_check(tmp_path: Path) -> None:
    at = start(FakeService(ask_result=withheld()), tmp_path)
    at.chat_input[0].set_value("How much is paid?").run()
    assert not at.exception, at.exception
    shown = text(at)
    assert "Withheld" in shown
    assert "Evidence checks failed" in shown
    assert "Numbers appear in a cited source (c1): 999 does not appear in any cited excerpt" in shown
    assert "It pays 999." not in shown


def test_service_error_shows_message_not_traceback(tmp_path: Path) -> None:
    at = start(None, tmp_path)
    assert not at.exception
    assert any("not configured" in e.value for e in at.error)


def test_kestrel_error_from_a_page_call_is_a_message(tmp_path: Path) -> None:
    fake = FakeService(documents_error=NotIngestedError("No documents yet; run kestrel ingest."))
    at = start(fake, tmp_path, page="views/documents.py")
    assert not at.exception, at.exception
    assert any("run kestrel ingest" in e.value for e in at.error)


def test_empty_states(tmp_path: Path) -> None:
    fake = FakeService()
    at = start(fake, tmp_path, page="views/documents.py")
    assert not at.exception, at.exception
    assert "No documents have been ingested" in text(at)
    at.switch_page("views/quality.py").run()
    assert not at.exception, at.exception
    shown = text(at)
    assert "No questions have been recorded" in shown
    assert "No evaluation runs found" in shown
    assert "The audit log has no records yet." in shown


def test_no_bots_is_an_empty_state(tmp_path: Path) -> None:
    at = start(FakeService(bots=()), tmp_path)
    assert not at.exception, at.exception
    assert "No assistant is available" in text(at)


def test_documents_page_shows_findings_and_blocks(tmp_path: Path) -> None:
    docs = [document("doc1"), document("doc0", DocumentStatus.SUPERSEDED, edition="2025", effective=None)]
    fake = FakeService(documents=docs, findings=[finding()], audit_records=[audit(1, citations=(citation(),))])
    at = start(fake, tmp_path, page="views/documents.py")
    assert not at.exception, at.exception
    shown = text(at)
    assert "Hidden text removed" in shown
    assert "hidden sample excerpt" in shown
    assert "Edition history: guide" in shown
    at.text_input[0].set_value("Wrong figures").run()
    next(b for b in at.button if b.label == "Block this document").click().run()
    assert not at.exception, at.exception
    assert fake.blocked == [("doc1", "Wrong figures")]
    assert "doc1.docx is now blocked." in text(at)


def test_non_operator_cannot_block(tmp_path: Path) -> None:
    at = start(FakeService(documents=[document()]), tmp_path, roles=("broker",), page="views/documents.py")
    assert not at.exception, at.exception
    assert not any(b.label == "Block this document" for b in at.button)
    assert "need the staff role" in text(at)


def test_quality_page_with_usage_and_runs(tmp_path: Path) -> None:
    from test_metrics import eval_record

    records = [
        audit(1, Outcome.ANSWERED, citations=(citation(),)),
        audit(2, Outcome.NOT_FOUND, question="Is dental cover offered?", detail="not in documents"),
        audit(3, Outcome.WITHHELD, digest="abc"),
    ]
    run = tmp_path / "20261120T000000Z_anthropic-claude-sonnet-5-5"
    run.mkdir()
    (run / "results.jsonl").write_text(
        "\n".join(eval_record(f"Q{n}", "correct").model_dump_json() for n in (1, 2)) + "\n", encoding="utf-8"
    )
    at = start(FakeService(audit_records=records), tmp_path, page="views/quality.py")
    assert not at.exception, at.exception
    shown = text(at)
    tables = "\n".join(df.value.to_string() for df in at.dataframe)
    assert "Is dental cover offered?" in tables
    assert "[not stored]" in tables
    assert "guide.docx > section 1" in tables  # most cited sections
    assert "2 of 2 (100%)" in tables  # retrieval hit rate of the run folder
    assert "Select a cell" in shown
    assert "Chain intact" in shown

