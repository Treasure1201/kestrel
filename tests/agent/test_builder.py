"""Agent construction: the tool inventory the model is actually offered, skills and MCP tools."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from fakes import ScriptedChatModel, final_answer, tool_call

from kestrel.agent.builder import build_agent
from kestrel.agent.context import Budget, RequestContext
from kestrel.agent.mcp_tools import load_mcp_tools
from kestrel.agent.middleware import is_transient
from kestrel.agent.tools import TOOL_NOTICE, build_builtin_tools
from kestrel.contracts import AgentAnswer, BotConfig, KnowledgeConfig, McpServerSpec, ModelPolicy, PrivacyPolicy, SearchScope
from kestrel.privacy import RuleBasedScreen

ECHO = Path(__file__).resolve().parents[1] / "spikes" / "mcp_echo_server.py"
FORBIDDEN = {"write_file", "edit_file", "delete", "execute", "task", "ls", "glob", "grep", "write_todos", "read_file"}
ANSWER = AgentAnswer(status="not_found", answer="The documents do not say.", reason="The documents do not say.")


def _bot(root: Path, *, tools=("search_documents", "get_table", "calculate"), skills=()) -> BotConfig:  # type: ignore[no-untyped-def]
    (root / "prompt.md").write_text("Persona text for tests.", encoding="utf-8")
    return BotConfig(
        id="demo", name="Demo", audience=frozenset({"agent"}), knowledge=KnowledgeConfig(collections=("demo",)),
        prompt_path=root / "prompt.md", prompt_text="Persona text for tests.", tools=tools, skills=tuple(skills),
        model=ModelPolicy(default="anthropic:claude-haiku-5-5"), root=root,
    )


def _ctx() -> RequestContext:
    return RequestContext(scope=SearchScope(collections=("demo",)), budget=Budget(max_model_calls=6, max_searches=3, max_repairs=1, timeout_s=60))


class _NoRetriever:
    def search(self, *a, **k):  # type: ignore[no-untyped-def]
        from kestrel.contracts import SearchResult

        return SearchResult()

    def get_table(self, *a, **k):  # type: ignore[no-untyped-def]
        return None


def _run(bot: BotConfig, script, ctx: RequestContext | None = None):  # type: ignore[no-untyped-def]
    ctx = ctx or _ctx()
    model = ScriptedChatModel(script=script)
    agent = build_agent(bot, model, build_builtin_tools(bot.tools, ctx, _NoRetriever()), ctx.budget)
    result = agent.invoke({"messages": [{"role": "user", "content": "question"}]})
    return model, result


def test_inventory_is_exactly_the_bot_tools_plus_the_answer_tool(tmp_path: Path) -> None:
    model, result = _run(_bot(tmp_path), [tool_call("search_documents", {"query": "q"}), final_answer(ANSWER)])
    for offered in model.offered_tools:
        assert sorted(offered) == ["AgentAnswer", "calculate", "get_table", "search_documents"]
    assert result["structured_response"] == ANSWER
    prompt = str(model.calls[0][0].content)
    assert "search_documents first" in prompt and "Persona text for tests." in prompt


def test_tool_not_in_bot_config_is_absent_and_refused(tmp_path: Path) -> None:
    model, result = _run(
        _bot(tmp_path, tools=("search_documents",)),
        [tool_call("calculate", {"expression": "1+1"}, "calc"), tool_call("write_file", {"file_path": "/x", "content": "y"}, "w"), final_answer(ANSWER)],
    )
    assert all(sorted(offered) == ["AgentAnswer", "search_documents"] for offered in model.offered_tools)
    outputs = {m.tool_call_id: str(m.content) for m in result["messages"] if type(m).__name__ == "ToolMessage"}
    assert outputs["calc"] == "Error: tool calculate is not available."
    assert outputs["w"] == "Error: tool write_file is not available."


def test_read_file_is_offered_only_with_skills(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "tables"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: tables\ndescription: Read banded tables.\n---\nRead every row.\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    model, result = _run(
        _bot(tmp_path, skills=[tmp_path / "skills"]),
        [
            tool_call("read_file", {"file_path": "/skills/tables/SKILL.md"}, "ok"),
            tool_call("read_file", {"file_path": "/skills/../secret.txt"}, "escape"),
            final_answer(ANSWER),
        ],
    )
    for offered in model.offered_tools:
        assert sorted(offered) == ["AgentAnswer", "calculate", "get_table", "read_file", "search_documents"]
        assert not (FORBIDDEN - {"read_file"}) & set(offered)
    outputs = {m.tool_call_id: str(m.content) for m in result["messages"] if type(m).__name__ == "ToolMessage"}
    assert "Read every row." in outputs["ok"]
    assert "SECRET" not in outputs["escape"]


def test_forced_tool_choice_falls_back_to_auto(tmp_path: Path) -> None:
    import httpx

    class Picky(ScriptedChatModel):
        choices: list = []  # type: ignore[type-arg]

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
            self.choices.append(tool_choice)
            return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
            if self.choices[-1] == "any":
                request = httpx.Request("POST", "https://x")
                raise httpx.HTTPStatusError("tool_choice: any is not supported", request=request, response=httpx.Response(400, request=request))
            return super()._generate(messages, stop, run_manager, **kwargs)

    ctx = _ctx()
    model = Picky(script=[final_answer(ANSWER)], model="picky-test-model")  # type: ignore[call-arg]
    bot = _bot(tmp_path)
    agent = build_agent(bot, model, build_builtin_tools(bot.tools, ctx, _NoRetriever()), ctx.budget)
    result = agent.invoke({"messages": [{"role": "user", "content": "question"}]})
    assert result["structured_response"] == ANSWER
    assert model.choices[:2] == ["any", None]
    assert ctx.budget.model_calls == 1


def test_transient_errors_only_are_retried() -> None:
    import httpx

    request = httpx.Request("POST", "https://x")
    assert is_transient(httpx.ConnectError("down"))
    assert is_transient(httpx.HTTPStatusError("busy", request=request, response=httpx.Response(529, request=request)))
    assert is_transient(httpx.HTTPStatusError("slow", request=request, response=httpx.Response(429, request=request)))
    assert not is_transient(httpx.HTTPStatusError("key", request=request, response=httpx.Response(401, request=request)))
    assert not is_transient(ValueError("bad request"))


def _spec(**changes) -> McpServerSpec:  # type: ignore[no-untyped-def]
    data = dict(
        name="echo", transport="stdio", command=str(Path(sys.executable).resolve()), args=(str(ECHO),),
        allowed_tools=("define",), locality="on-device",
        read_only={"reviewed_by": "t", "reviewed_on": "2026-11-22", "statement": "reads a dict"},
    )
    data.update(changes)
    return McpServerSpec.model_validate(data)


def test_mcp_tools_are_allow_listed_screened_and_wrapped() -> None:
    async def main():  # type: ignore[no-untyped-def]
        loaded = await load_mcp_tools([_spec()], RuleBasedScreen(), PrivacyPolicy(), reserved=["search_documents"])
        tools = {tool.name: tool for tool in loaded.tools}
        reply = await tools["define"].ainvoke({"term": "Waiting period"})
        redacted = await tools["define"].ainvoke({"term": "email me at someone@example.com"})
        return sorted(tools), reply, redacted, loaded.warnings

    names, reply, redacted, warnings = asyncio.run(asyncio.wait_for(main(), timeout=90))
    assert names == ["define"]  # `add` is on the server but not allowed
    assert reply.startswith(TOOL_NOTICE)
    assert "Time after a claim event before benefits start." in reply
    assert "unknown term" in redacted
    assert warnings == []


def test_mcp_block_policy_refuses_personal_information() -> None:
    async def main():  # type: ignore[no-untyped-def]
        loaded = await load_mcp_tools([_spec()], RuleBasedScreen(), PrivacyPolicy(on_personal_information="block"))
        return await loaded.tools[0].ainvoke({"term": "someone@example.com"})

    assert "personal information" in asyncio.run(asyncio.wait_for(main(), timeout=90))


def test_mcp_server_that_fails_to_start_is_a_warning() -> None:
    async def main():  # type: ignore[no-untyped-def]
        missing = _spec(name="ghost", command="definitely-not-a-real-command-xyz", args=())
        broken = _spec(name="broken", args=(str(ECHO.with_name("does_not_exist.py")),))
        return await load_mcp_tools([missing, broken], RuleBasedScreen(), PrivacyPolicy())

    loaded = asyncio.run(asyncio.wait_for(main(), timeout=90))
    assert loaded.tools == []
    assert len(loaded.warnings) == 2 and all("could not be started" in w for w in loaded.warnings)


def test_budget_has_a_model_call_limit_and_a_deadline() -> None:
    from kestrel.errors import BudgetExceededError

    budget = Budget(max_model_calls=1, max_searches=1, max_repairs=0, timeout_s=10)
    budget.take_model_call()
    with pytest.raises(BudgetExceededError):
        budget.take_model_call()
    expired = Budget(max_model_calls=5, max_searches=1, max_repairs=0, timeout_s=1, started=time.monotonic() - 5)
    with pytest.raises(BudgetExceededError):
        expired.take_model_call()


class _ResponseError(Exception):
    """Stands in for the error a local provider raises when it cannot parse the model's tool call."""


class _Garbled(ScriptedChatModel):
    """Raises a tool-call parsing error on the first `garbled` calls, then replays the script."""

    garbled: int = 1
    failures: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        if self.failures < self.garbled:
            self.failures += 1
            raise _ResponseError("error parsing tool call: raw='We need to produce correct AgentAnswer JSON. Must'")
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_a_malformed_tool_call_is_asked_again_within_the_budget(tmp_path: Path) -> None:
    from kestrel.agent.middleware import MALFORMED_NOTE, is_malformed_tool_call

    assert is_malformed_tool_call(_ResponseError("error parsing tool call: raw='x'"))
    assert not is_malformed_tool_call(_ResponseError("connection refused"))
    ctx = _ctx()
    model = _Garbled(script=[final_answer(ANSWER)])
    agent = build_agent(_bot(tmp_path), model, build_builtin_tools(_bot(tmp_path).tools, ctx, _NoRetriever()), ctx.budget)
    result = asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "question"}]}))
    assert result["structured_response"] == ANSWER
    assert ctx.budget.model_calls == 2  # the failed call and the retry both count
    assert MALFORMED_NOTE in str(model.calls[-1][-1].content)


def test_a_malformed_tool_call_is_raised_when_retries_or_budget_run_out(tmp_path: Path) -> None:
    ctx = _ctx()
    model = _Garbled(script=[final_answer(ANSWER)], garbled=5)
    agent = build_agent(_bot(tmp_path), model, build_builtin_tools(_bot(tmp_path).tools, ctx, _NoRetriever()), ctx.budget)
    with pytest.raises(_ResponseError):
        asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "question"}]}))
    assert ctx.budget.model_calls == 3  # one call and two retries
    tight = RequestContext(scope=SearchScope(collections=("demo",)), budget=Budget(max_model_calls=1, max_searches=1, max_repairs=0, timeout_s=60))
    model = _Garbled(script=[final_answer(ANSWER)])
    agent = build_agent(_bot(tmp_path), model, build_builtin_tools(_bot(tmp_path).tools, tight, _NoRetriever()), tight.budget)
    with pytest.raises(_ResponseError):
        asyncio.run(agent.ainvoke({"messages": [{"role": "user", "content": "question"}]}))
    assert tight.budget.model_calls == 1
