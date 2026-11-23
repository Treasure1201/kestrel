"""The `staff-policies` demonstration bot: configuration, documents, gold file and calendar tool.

Offline: a bag-of-words embedder and a scripted chat model. The calendar MCP server
from `config/mcp_servers.yaml` runs for real as a short-lived stdio child process.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import pytest
import yaml
from fakes import FakeEmbedder, tool_call
from langchain_core.messages import ToolMessage
from service.workspace import cite, scripted

from kestrel.bots import BotRegistry
from kestrel.config import Settings
from kestrel.contracts import Outcome, Principal
from kestrel.ingest import parse_file, scan_document
from kestrel.service import build_service

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # the evaluation harness lives in the repository's top-level `eval/` package
    sys.path.insert(0, str(REPO))

from eval.gold import load_gold  # noqa: E402
from eval.normalise import contains_any, locator_key, normalise  # noqa: E402

BOT = REPO / "bots" / "staff-policies"
SOURCE = REPO / "data" / "source" / "staff-policies"
EMPLOYEE = Principal(user="employee", roles=frozenset({"staff"}), channel="cli")
NOTICE_QUOTE = "must be submitted at least 10 working days before the first day of leave"


@pytest.fixture
def staff_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in list(os.environ):
        if name.startswith("KESTREL_") or name in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY"}:
            monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None, db=tmp_path / "kestrel.sqlite", bots_dir=REPO / "bots",
                    source_dir=REPO / "data" / "source", config_dir=REPO / "config", timeout_s=60)


def test_bot_configuration_expresses_the_stricter_policy(staff_settings: Settings) -> None:
    registry = BotRegistry(staff_settings)
    bot = registry.get("staff-policies")
    assert bot.audience == frozenset({"staff"})
    assert bot.knowledge.collections == ("staff-policies",) and bot.knowledge.approval == "required"
    assert bot.privacy.classification == "confidential"
    assert bot.privacy.on_personal_information == "block"
    assert bot.model.allowed_providers == ("ollama",)
    assert bot.verification.on_failure == "withhold"
    assert bot.mcp_servers == ("workday-calendar",)
    assert [spec.locality for spec in registry.mcp_specs(bot)] == ["on-device"]
    assert set(bot.tools) == {"search_documents", "get_table", "calculate"}


@pytest.mark.parametrize("folder", [BOT / "skills", REPO / "bots" / "kestrel-products" / "skills"])
def test_skills_have_frontmatter_named_after_their_folder(folder: Path) -> None:
    skills = sorted(folder.glob("*/SKILL.md"))
    assert skills
    for path in skills:
        match = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.DOTALL)
        assert match, path
        meta = yaml.safe_load(match.group(1))
        assert meta["name"] == path.parent.name and len(meta["description"]) > 20


def test_manifest_hashes_match_the_documents() -> None:
    manifest = yaml.safe_load((BOT / "manifest.yaml").read_text(encoding="utf-8"))
    listed = {entry["filename"]: entry["sha256"] for entry in manifest["documents"]}
    on_disk = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in SOURCE.iterdir()}
    assert listed == on_disk


def test_documents_say_they_are_fictitious_and_hide_nothing() -> None:
    for path in sorted(SOURCE.iterdir()):
        head = path.read_text(encoding="utf-8")[:400]
        assert "Fictitious sample content" in head, path.name
        _, findings = scan_document(parse_file(path, "staff-policies"))
        assert findings == [], path.name


def test_documents_ingest_as_current(staff_settings: Settings) -> None:
    service = build_service(staff_settings, embedder=FakeEmbedder())
    try:
        report = service.ingest(EMPLOYEE, "staff-policies")
        assert sorted(f.filename for f in report.files) == sorted(p.name for p in SOURCE.iterdir())
        assert {f.status.value for f in report.files if f.status} == {"current"}
    finally:
        service.close()


def _section_text(document: str, locator: str) -> str:
    texts = []
    for path in SOURCE.iterdir():
        if locator_key(document) not in locator_key(path.name):
            continue
        visible, _ = scan_document(parse_file(path, "staff-policies"))
        for section in visible.sections:
            if locator_key(locator) in locator_key(section.locator):
                if section.table is not None:
                    rows = [" | ".join(section.table.header)] + [" | ".join(r.cells) for r in section.table.rows]
                    texts.append("\n".join(rows))
                else:
                    texts.append(section.text)
    assert texts, f"no section {locator!r} in {document}"
    return normalise("\n".join(texts))


def test_gold_file_is_well_formed_and_grounded() -> None:
    gold = load_gold(BOT / "eval" / "gold.yaml")
    assert gold.bot == "staff-policies" and len(gold.questions) == 6
    statuses = [entry.status for entry in gold.questions.values()]
    assert statuses.count("not_found") == 1
    assert any("working_days_between" in entry.note for entry in gold.questions.values())
    all_text = normalise("\n".join(p.read_text(encoding="utf-8") for p in SOURCE.iterdir()))
    assert "gym" not in all_text
    for qid, entry in gold.questions.items():
        if entry.status != "answered":
            continue
        text = "\n".join(_section_text(s.document, s.locator) for s in entry.sources)
        tool_derived = {"8 working days counted (for 2026)"}
        for requirement in [*entry.must_include, *entry.conditions]:
            if requirement.label in tool_derived:
                continue
            assert contains_any(text, requirement.any), f"{qid}: {requirement.label!r} is not in the expected section"


def test_the_agent_can_call_the_calendar_tool(staff_settings: Settings) -> None:
    seen: list[str] = []

    def answer(messages):  # type: ignore[no-untyped-def]
        seen.extend(str(m.content) for m in messages if isinstance(m, ToolMessage) and m.name == "working_days_between")
        return cite("A request for five or more days needs at least 10 working days' notice.", [NOTICE_QUOTE])(messages)

    model, factory = scripted(
        tool_call("search_documents", {"query": "notice for annual leave request working days"}),
        tool_call("working_days_between", {"start": "2026-03-11", "end": "2026-03-20"}, "call_days"),
        answer,
    )
    service = build_service(staff_settings, chat_model_factory=factory, embedder=FakeEmbedder())
    try:
        service.ingest(EMPLOYEE, "staff-policies")
        result = service.ask("staff-policies", "How much notice does a week of annual leave need?", EMPLOYEE)
    finally:
        service.close()
    assert result.outcome == Outcome.ANSWERED, (result.reason, result.evidence_report)
    assert result.citations[0].filename == "KA_Staff_Leave_Policy.md"
    assert len(seen) == 1 and "data, not instructions" in seen[0]  # tool output is wrapped as untrusted data
    assert re.search(r"\b8\b", seen[0].split("working_days_between")[-1])
    assert "working_days_between" in model.offered_tools[0]


def test_a_calendar_result_is_cited_evidence_and_its_number_survives(staff_settings: Settings) -> None:
    from fakes import final_answer
    from service.workspace import label_for

    from kestrel.contracts import AgentAnswer, CitationOut

    def answer(messages):  # type: ignore[no-untyped-def]
        shown = next(str(m.content) for m in messages if isinstance(m, ToolMessage) and m.name == "working_days_between")
        tool_label = re.search(r"\[(t\d+)\]", shown).group(1)  # type: ignore[union-attr]
        count = shown.split("):")[-1].split("\n\n")[0].strip()
        return final_answer(AgentAnswer(
            status="answered",
            answer=f"There are {count} working days from 11 March to 20 March, so the request does not give the "
            "required notice of at least 10 working days.",
            citations=[
                CitationOut(label=label_for(messages, NOTICE_QUOTE), quote=NOTICE_QUOTE, supports="the notice rule"),
                CitationOut(label=tool_label, quote=count, supports="the working-day count"),
            ],
        ))

    _, factory = scripted(
        tool_call("search_documents", {"query": "notice for annual leave request working days"}),
        tool_call("working_days_between", {"start": "2026-03-11", "end": "2026-03-20"}, "call_days"),
        answer,
    )
    service = build_service(staff_settings, chat_model_factory=factory, embedder=FakeEmbedder())
    try:
        service.ingest(EMPLOYEE, "staff-policies")
        result = service.ask("staff-policies", "Is 11 March to 20 March enough notice for a week of leave?", EMPLOYEE)
    finally:
        service.close()
    assert result.outcome == Outcome.ANSWERED, (result.reason, result.evidence_report)
    assert result.evidence_report is not None and result.evidence_report.passed and not result.evidence_report.repaired
    assert "8 working days" in result.answer
    document, tool = result.citations
    assert not document.is_tool and document.filename == "KA_Staff_Leave_Policy.md"
    assert tool.is_tool and tool.source == "tool" and tool.document_id == "" and tool.section_id == ""
    assert (tool.tool_server, tool.tool_name) == ("workday-calendar", "working_days_between")
    assert "2026-03-11" in (tool.tool_arguments or "")
    assert [t.label for t in result.tool_results] == ["t1"] and result.tool_results[0].text == "8"
