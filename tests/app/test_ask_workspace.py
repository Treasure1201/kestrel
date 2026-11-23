"""The Ask workspace: selecting answers, the evidence tabs, repaired checks and tool output (fake service)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app_fakes import FakeService, answered, not_found, withheld
from streamlit.testing.v1 import AppTest
from test_pages import start, text

from kestrel.contracts import REPAIRED_PREFIX, AskResult, CheckIssue, EvidenceReport, ResolvedCitation

TABS = ("Sources (", "How it was answered", "Checks", "Other passages (")


class ToolCitation(ResolvedCitation):
    """A citation carrying the tool fields a result uses to mark evidence that came from a tool."""

    source: str = "tool"
    tool_server: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None


def evidence_question(at: AppTest) -> str:
    """The question named in the evidence pane's header."""
    header = next(str(m.value) for m in at.markdown if 'class="k-evq"' in str(m.value))
    return header.split('class="k-evq"', 1)[1].split(">", 1)[1].split("<", 1)[0]


def tab_labels(at: AppTest) -> list[str]:
    return [str(t.label) for t in at.tabs]


def ask(at: AppTest, question: str) -> None:
    at.chat_input[0].set_value(question).run()
    assert not at.exception, at.exception


def test_a_new_answer_selects_its_evidence(tmp_path: Path) -> None:
    fake = FakeService(ask_result=answered())
    at = start(fake, tmp_path)
    ask(at, "What is the waiting period?")
    assert evidence_question(at) == "What is the waiting period?"
    fake.ask_result = not_found()
    ask(at, "Is dental cover offered?")
    assert evidence_question(at) == "Is dental cover offered?"
    assert "Sources (0)" in tab_labels(at)
    assert "A decline cites no source." in text(at)


def test_show_evidence_switches_without_asking_again(tmp_path: Path) -> None:
    fake = FakeService(ask_result=answered())
    at = start(fake, tmp_path)
    ask(at, "What is the waiting period?")
    fake.ask_result = not_found()
    ask(at, "Is dental cover offered?")
    assert len(fake.asked) == 2
    show = [b for b in at.button if b.label == "Show evidence"]
    assert len(show) == 1  # only the answer that is not selected offers it
    show[0].click().run()
    assert not at.exception, at.exception
    assert len(fake.asked) == 2
    assert evidence_question(at) == "What is the waiting period?"
    assert "Sources (1)" in tab_labels(at)
    assert "the waiting period is 3 months" in text(at)
    assert sum(b.label == "Show evidence" for b in at.button) == 1  # now offered on the newer answer


@pytest.mark.parametrize("result", [answered(), not_found(), withheld()], ids=["answered", "declined", "withheld"])
def test_four_tabs_for_each_outcome(result: AskResult, tmp_path: Path) -> None:
    at = start(FakeService(ask_result=result), tmp_path)
    ask(at, result.question)
    labels = tab_labels(at)
    assert len(labels) == 4
    assert all(label.startswith(prefix) for label, prefix in zip(labels, TABS, strict=True))
    shown = text(at)
    assert "Audit record written" in shown  # the timeline rendered
    assert "does not mean the answer is right" in shown  # the checks tab note rendered


def test_withheld_lists_the_failed_check_with_an_explanation(tmp_path: Path) -> None:
    at = start(FakeService(ask_result=withheld()), tmp_path)
    ask(at, "How much is paid?")
    shown = text(at)
    assert "Still failing" in shown
    assert "A number in the answer does not appear in any cited source." in shown


def test_before_repair_items_are_fixed_not_failed(tmp_path: Path) -> None:
    report = EvidenceReport(passed=True, repaired=True, issues=(
        CheckIssue(check="quote_verbatim", severity="warning", detail=f"{REPAIRED_PREFIX}quote not found", label="c1"),
    ))
    at = start(FakeService(ask_result=answered().model_copy(update={"evidence_report": report})), tmp_path)
    ask(at, "What is the waiting period?")
    checks = next(str(m.value) for m in at.markdown if '<ul class="k-checklist">' in str(m.value))
    assert "Fixed during repair: Quote matches the source word for word" in checks
    assert "Failed:" not in checks and "Still failing" not in checks
    assert REPAIRED_PREFIX not in text(at)


def test_tool_citation_renders_as_tool_output(tmp_path: Path) -> None:
    base = answered().citations[0]
    cited = ToolCitation(**base.model_dump(), tool_server="rates-server", tool_name="lookup_rate",
                         tool_arguments='{"band": 5}')
    result = answered().model_copy(update={"citations": (cited,)})
    at = start(FakeService(ask_result=result), tmp_path)
    ask(at, "What is the waiting period?")
    shown = text(at)
    assert "Tool output" in shown
    assert "lookup_rate" in shown and "on server rates-server" in shown and '{"band": 5}' in shown
    assert "Tool output, shown as data only" in shown
    assert not any(b.label == "Open section" for b in at.button)  # not shown as a document passage
    assert "c1 · tool lookup_rate" in shown  # the chip on the answer card
