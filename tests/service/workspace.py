"""A throw-away workspace with two synthetic bots, their documents and manifests, for service tests."""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fakes import FakeEmbedder, ScriptedChatModel, final_answer
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from kestrel.config import Settings
from kestrel.contracts import AgentAnswer, CalculationOut, CitationOut, Principal
from kestrel.service import LocalAssistantService, build_service

REPO = Path(__file__).resolve().parents[2]
ECHO_SERVER = REPO / "tests" / "spikes" / "mcp_echo_server.py"

GUIDE_2026 = """# Benefit Guide 2026

## Waiting periods

Option A: the waiting period is one month. Option B: the waiting period is three months.

## Claims

The team aims to make a decision within 10 working days of receiving all required documents.

## Benefit amount

The monthly benefit is 75% of monthly salary, up to a maximum of R50 000 per month.

## Free cover limits

| Band | Members from | Members to | Free cover limit (R) |
| --- | --- | --- | --- |
| 1 | 10 | 49 | R 1 000 000 |
| 2 | 50 | 99 | R 2 000 000 |
| 3 | 100 | 249 | R 3 500 000 |
"""

GUIDE_2025 = """# Benefit Guide 2025

## Waiting periods

Option B: the waiting period is six months.
"""

PROMPT = "You help staff answer questions about the demo benefit guide."


def _bot_yaml(bot_id: str, collection: str, *, audience: str, classification: str, on_pi: str, audit_text: str,
              on_failure: str, mcp: Sequence[str], tools: str) -> str:
    return f"""id: {bot_id}
name: {bot_id.title()} Assistant
description: Synthetic test bot.
audience: [{audience}]
knowledge:
  collections: [{collection}]
  approval: required
prompt: prompt.md
tools: {tools}
mcp_servers: [{", ".join(mcp)}]
model:
  default: anthropic:claude-haiku-5-5
  max_model_calls: 8
  max_searches: 3
privacy:
  classification: {classification}
  on_personal_information: {on_pi}
  audit_question_text: {audit_text}
verification:
  on_failure: {on_failure}
  max_repairs: 1
"""


def _manifest(collection: str, files: dict[str, bytes]) -> str:
    lines = [f"collection: {collection}", "documents:"]
    editions = {"guide_2025.md": ("2025", "2025-01-01", None), "guide_2026.md": ("2026", "2026-01-01", "2025")}
    for name, data in files.items():
        edition, effective, supersedes = editions[name]
        lines += [
            f"  - filename: {name}",
            f"    sha256: {hashlib.sha256(data).hexdigest()}",
            f"    label: Benefit Guide {edition}",
            "    family: benefit-guide",
            f'    edition: "{edition}"',
            f"    effective_from: {effective}",
            "    approved: true",
            "    approved_by: test",
        ]
        if supersedes:
            lines.append(f'    supersedes: "{supersedes}"')
    return "\n".join(lines) + "\n"


def make_workspace(
    root: Path,
    *,
    on_pi: str = "redact",
    audit_text: str = "redacted",
    on_failure: str = "withhold",
    secret_classification: str = "confidential",
    mcp_server: bool = False,
    tools: str = "[search_documents, get_table, calculate]",
) -> Settings:
    """Create bots `demo` (audience agent) and `secret` (audience hr), documents and config; return settings."""
    files = {"guide_2025.md": GUIDE_2025.encode(), "guide_2026.md": GUIDE_2026.encode()}
    config = root / "config"
    config.mkdir(parents=True)
    for name in ("models.yaml", "pricing.yaml"):
        shutil.copy(REPO / "config" / name, config / name)
    mcp_names: list[str] = []
    if mcp_server:
        mcp_names = ["echo"]
        (config / "mcp_servers.yaml").write_text(
            "servers:\n  - name: echo\n    transport: stdio\n"
            f"    command: {Path(sys.executable).resolve().as_posix()!r}\n"
            f"    args: [{ECHO_SERVER.as_posix()!r}]\n"
            "    allowed_tools: [define]\n    locality: on-device\n"
            "    read_only: {reviewed_by: test, reviewed_on: '2026-11-22', statement: reads a dict}\n",
            encoding="utf-8",
        )
    for bot_id, collection, audience, classification in (
        ("demo", "demo-docs", "agent", "internal"),
        ("secret", "secret-docs", "hr", secret_classification),
    ):
        folder = root / "bots" / bot_id
        folder.mkdir(parents=True)
        (folder / "prompt.md").write_text(PROMPT, encoding="utf-8")
        (folder / "bot.yaml").write_text(
            _bot_yaml(bot_id, collection, audience=audience, classification=classification, on_pi=on_pi,
                      audit_text=audit_text, on_failure=on_failure, mcp=mcp_names if bot_id == "demo" else [], tools=tools),
            encoding="utf-8",
        )
        (folder / "manifest.yaml").write_text(_manifest(collection, files), encoding="utf-8")
        source = root / "source" / collection
        source.mkdir(parents=True)
        for name, data in files.items():
            (source / name).write_bytes(data)
    return Settings(
        _env_file=None, db=root / "kestrel.sqlite", bots_dir=root / "bots", source_dir=root / "source", config_dir=config, timeout_s=60,
    )


AGENT = Principal(user="tester", roles=frozenset({"agent"}), channel="cli")
OPERATOR = Principal(user="op", roles=frozenset({"agent", "hr", "staff"}), channel="cli")
OUTSIDER = Principal(user="nobody", roles=frozenset({"visitor"}), channel="cli")


def service_with(settings: Settings, factory: Callable[[str], ScriptedChatModel], *, ingest: bool = True) -> LocalAssistantService:
    service = build_service(settings, chat_model_factory=factory, embedder=FakeEmbedder())
    if ingest:
        service.ingest(OPERATOR)
    return service


class ReactiveChatModel(ScriptedChatModel):
    """A scripted model whose replies may be callables of the messages so far (to cite labels it was shown)."""

    script: list[Any]  # type: ignore[assignment]

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        index = len(self.calls)
        self.calls.append(list(messages))
        self.offered_tools.append(list(self.pending_tools))
        if index >= len(self.script):
            raise AssertionError(f"model called {index + 1} times but only {len(self.script)} replies scripted")
        reply = self.script[index]
        reply = reply(messages) if callable(reply) else reply
        return ChatResult(generations=[ChatGeneration(message=reply.model_copy(deep=True))])


def label_for(messages: Sequence[BaseMessage], needle: str) -> str:
    """The label of the excerpt or row, among tool results so far, whose text contains `needle`."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        content = str(message.content)
        index = content.find(needle)
        if index >= 0:
            labels = re.findall(r"\[(c\d+(?:\.r\d+)?)\]", content[:index])
            if labels:
                return labels[-1]
    raise AssertionError(f"{needle!r} was not shown to the model")


def cite(text: str, quotes: Sequence[str], *, status: str = "answered", calculations: Sequence[tuple[str, str, str]] = (),
         caveats: Sequence[str] = (), reason: str | None = None) -> Callable[[Sequence[BaseMessage]], AIMessage]:
    """A reply that answers with citations to whichever labels hold `quotes` (a calculation rule may name a quote)."""

    def reply(messages: Sequence[BaseMessage]) -> AIMessage:
        citations = [CitationOut(label=label_for(messages, q), quote=q, supports="the answer") for q in quotes]
        calcs = [CalculationOut(expression=e, result=r, rule=label_for(messages, rule)) for e, r, rule in calculations]
        return final_answer(AgentAnswer(status=status, answer=text, citations=citations, calculations=calcs,  # type: ignore[arg-type]
                                        caveats=list(caveats), reason=reason))

    return reply


def scripted(*replies: Any) -> tuple[ReactiveChatModel, Callable[[str], ReactiveChatModel]]:
    """One scripted model shared by every request, and a factory returning it."""
    model = ReactiveChatModel(script=list(replies))
    return model, lambda _ref: model


def decline(reason: str = "The documents do not state this.", status: str = "not_found") -> AIMessage:
    return final_answer(AgentAnswer(status=status, answer=reason, reason=reason))  # type: ignore[arg-type]
