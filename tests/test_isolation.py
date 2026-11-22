"""Two real bots side by side: each sees only its own documents, roles, tools and policy.

Offline: the repository's own `bots/` and `data/source/` folders, a bag-of-words
embedder and a scripted chat model. The staff bot's on-device calendar server is
started for real (a short-lived stdio child process) when its agent is built.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import FakeEmbedder, ScriptedChatModel, final_answer, tool_call

from kestrel.config import Settings
from kestrel.contracts import AgentAnswer, Outcome, Principal
from kestrel.errors import AccessDeniedError, InvalidInputError
from kestrel.service import LocalAssistantService, build_service

REPO = Path(__file__).resolve().parents[1]
PRODUCTS, STAFF = "kestrel-products", "staff-policies"
CALENDAR_TOOLS = {"today", "working_days_between"}
# A syntactically valid, made-up South African ID number (date 1980-01-01, checksum valid).
ID_NUMBER = "8001015009087"

EMPLOYEE = Principal(user="employee", roles=frozenset({"staff"}), channel="cli")
AGENT = Principal(user="agent", roles=frozenset({"call-centre-agent"}), channel="cli")
BOTH = Principal(user="operator", roles=frozenset({"staff", "call-centre-agent"}), channel="cli")


def _settings(root: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None, db=root / "kestrel.sqlite", bots_dir=REPO / "bots", source_dir=REPO / "data" / "source",
        config_dir=REPO / "config", model="anthropic:claude-haiku-5-5", timeout_s=60, **overrides,
    )


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A database with both bots' collections ingested once for the whole module."""
    root = tmp_path_factory.mktemp("isolation")
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith("KESTREL_") or name in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY"}:
                patch.delenv(name, raising=False)
        service = build_service(_settings(root), embedder=FakeEmbedder())
        report = service.ingest(BOTH)
        service.close()
        assert {f.collection for f in report.files} == {PRODUCTS, STAFF}
        assert all(f.action != "failed" for f in report.files), [f.detail for f in report.files]
        yield root


def _service(root: Path, model: ScriptedChatModel | None = None, **overrides: object) -> LocalAssistantService:
    scripted = model or ScriptedChatModel(script=[])
    return build_service(_settings(root, **overrides), chat_model_factory=lambda _ref: scripted, embedder=FakeEmbedder())


@pytest.fixture
def service(workspace: Path) -> Iterator[LocalAssistantService]:
    svc = _service(workspace)
    yield svc
    svc.close()


def _decline(reason: str = "The documents do not state this.") -> ScriptedChatModel:
    return ScriptedChatModel(script=[
        tool_call("search_documents", {"query": "annual leave"}),
        final_answer(AgentAnswer(status="not_found", answer=reason, reason=reason)),
    ])


def test_both_bots_load_from_configuration(service: LocalAssistantService) -> None:
    bots = {bot.id: bot for bot in service.list_bots(BOTH)}
    assert set(bots) == {PRODUCTS, STAFF}
    assert bots[STAFF].collections == (STAFF,) and bots[PRODUCTS].collections == (PRODUCTS,)
    assert bots[STAFF].classification == "confidential"
    assert bots[STAFF].model.startswith("ollama:")


@pytest.mark.parametrize(
    ("bot_id", "own", "query"),
    [
        # Each query's best match is in the OTHER bot's documents.
        (PRODUCTS, PRODUCTS, "annual leave working days completed years of service home-office allowance"),
        (STAFF, STAFF, "group life free cover limit scheme members disability income waiting period Option B"),
    ],
)
def test_search_returns_only_the_bots_own_collection(service: LocalAssistantService, bot_id: str, own: str, query: str) -> None:
    hits = service.search(bot_id, query, BOTH, limit=20).hits
    assert hits, "the bot's own documents should still match some words"
    own_files = {d.filename for d in service.list_documents(bot_id, BOTH)}
    assert {hit.filename for hit in hits} <= own_files
    assert all(d.collection == own for d in service.list_documents(bot_id, BOTH))


def test_the_other_bot_really_holds_the_best_match(service: LocalAssistantService) -> None:
    staff_hits = service.search(STAFF, "annual leave working days completed years of service", BOTH).hits
    assert staff_hits[0].filename == "KA_Staff_Leave_Policy.md"
    product_hits = service.search(PRODUCTS, "group life free cover limit scheme members", BOTH).hits
    assert product_hits[0].filename.startswith("KA_")
    assert not product_hits[0].filename.endswith(".md")


def test_get_section_across_bots_is_refused(service: LocalAssistantService) -> None:
    staff_section = service.search(STAFF, "home-office allowance", BOTH).hits[0].section_id
    product_section = service.search(PRODUCTS, "funeral cover child", BOTH).hits[0].section_id
    assert service.get_section(STAFF, staff_section, BOTH).section_id == staff_section
    with pytest.raises(InvalidInputError):
        service.get_section(PRODUCTS, staff_section, BOTH)
    with pytest.raises(InvalidInputError):
        service.get_section(STAFF, product_section, BOTH)


def test_a_principal_without_the_staff_role_cannot_see_or_use_the_staff_bot(workspace: Path) -> None:
    model = ScriptedChatModel(script=[])
    svc = _service(workspace, model)
    try:
        assert [bot.id for bot in svc.list_bots(AGENT)] == [PRODUCTS]
        result = svc.ask(STAFF, "How many days of annual leave do I get?", AGENT)
        assert result.outcome == Outcome.REFUSED and result.error_category == "access_denied"
        assert model.calls == []
        with pytest.raises(AccessDeniedError):
            svc.search(STAFF, "annual leave", AGENT)
        with pytest.raises(AccessDeniedError):
            svc.list_documents(STAFF, AGENT)
        section = svc.search(STAFF, "annual leave", EMPLOYEE).hits[0].section_id
        with pytest.raises(AccessDeniedError):
            svc.get_section(STAFF, section, AGENT)
        # And the other way round: staff alone do not see the product bot.
        assert [bot.id for bot in svc.list_bots(EMPLOYEE)] == [STAFF]
    finally:
        svc.close()


def test_staff_bot_refuses_a_hosted_model_before_calling_it(workspace: Path) -> None:
    model = ScriptedChatModel(script=[])
    svc = _service(workspace, model)
    try:
        result = svc.ask(STAFF, "How many days of annual leave do I get?", EMPLOYEE, model="anthropic:claude-haiku-5-5")
        assert result.outcome == Outcome.REFUSED and result.error_category == "egress_denied"
        assert model.calls == []
    finally:
        svc.close()


def test_staff_bot_refuses_ollama_that_is_not_on_this_machine(workspace: Path) -> None:
    model = ScriptedChatModel(script=[])
    svc = _service(workspace, model, ollama_base_url="http://ollama.example.com:11434")
    try:
        result = svc.ask(STAFF, "How many days of annual leave do I get?", EMPLOYEE)
        assert result.outcome == Outcome.REFUSED and result.error_category == "egress_denied"
        assert "on-device" in (result.reason or "")
        assert model.calls == []
    finally:
        svc.close()


def test_staff_bot_accepts_local_ollama_on_loopback(workspace: Path) -> None:
    model = _decline()
    svc = _service(workspace, model)  # default ollama_base_url is http://127.0.0.1:11434
    try:
        result = svc.ask(STAFF, "Is there a policy on gym membership?", EMPLOYEE)
        assert result.model.startswith("ollama:")
        assert result.outcome == Outcome.NOT_FOUND, result.reason
        assert len(model.calls) == 2
    finally:
        svc.close()


def test_personal_information_is_blocked_for_staff_but_redacted_for_products(workspace: Path) -> None:
    staff_model = ScriptedChatModel(script=[])
    svc = _service(workspace, staff_model)
    try:
        blocked = svc.ask(STAFF, f"My ID number is {ID_NUMBER}. How much leave do I get?", EMPLOYEE)
        assert blocked.outcome == Outcome.REFUSED and blocked.error_category == "privacy_blocked"
        assert staff_model.calls == [] and ID_NUMBER not in blocked.question
    finally:
        svc.close()

    product_model = _decline()
    svc = _service(workspace, product_model)
    try:
        redacted = svc.ask(PRODUCTS, f"My ID number is {ID_NUMBER}. What is the waiting period for Option B?", AGENT)
        assert redacted.outcome == Outcome.NOT_FOUND and "sa_id" in redacted.redactions
        sent = " ".join(str(m.content) for call in product_model.calls for m in call)
        assert ID_NUMBER not in sent and product_model.calls
    finally:
        svc.close()


def test_only_the_staff_bot_is_offered_the_calendar_tools(workspace: Path) -> None:
    offered: dict[str, set[str]] = {}
    for bot_id, principal in ((STAFF, EMPLOYEE), (PRODUCTS, AGENT)):
        model = _decline()
        svc = _service(workspace, model)
        try:
            result = svc.ask(bot_id, "Is there a rule about dental cover?", principal)
            assert result.outcome == Outcome.NOT_FOUND, result.reason
            offered[bot_id] = set(model.offered_tools[0])
        finally:
            svc.close()
    assert CALENDAR_TOOLS <= offered[STAFF]
    assert not CALENDAR_TOOLS & offered[PRODUCTS]
    for tools in offered.values():
        assert {"search_documents", "get_table", "calculate", "read_file"} <= tools  # both bots have skills
        assert not {"write_file", "edit_file", "execute", "task", "ls", "glob", "grep"} & tools
