"""Spike: a DeepAgents agent with only read-only tools, read-only skills and structured output.

The real `create_deep_agent` runtime runs against `ScriptedChatModel`, which records
the tool names bound for every model call, so these tests check what the model was
actually offered, not what the configuration intended.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import ScriptedChatModel, final_answer, tool_call
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from spikes.readonly_agent import build_read_only_agent

from kestrel.contracts import AgentAnswer, CalculationOut, CitationOut

pytestmark = pytest.mark.spike

FORBIDDEN = {"write_file", "edit_file", "delete", "execute", "task", "ls", "glob", "grep", "write_todos"}
SKILL_BODY = "Read every row of a banded table before choosing a band."
SECRET = "SECRET-OUTSIDE-SKILLS"


@tool
def search_documents(query: str) -> str:
    """Search the approved documents and return labelled excerpts."""
    return "[c1] Option B: the waiting period is three months."


@pytest.fixture
def bot_folder(tmp_path: Path) -> Path:
    skill = tmp_path / "skills" / "table-lookup"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: table-lookup\ndescription: How to read banded tables.\n---\n# Table lookup\n{SKILL_BODY}\n",
        encoding="utf-8",
    )
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return tmp_path


def _run(model: ScriptedChatModel, bot_folder: Path) -> dict:
    agent = build_read_only_agent(model, [search_documents], bot_folder / "skills", "You answer from documents only.")
    return agent.invoke({"messages": [{"role": "user", "content": "What is the Option B waiting period?"}]})


def _tool_results(result: dict) -> dict[str, str]:
    return {m.tool_call_id: str(m.content) for m in result["messages"] if isinstance(m, ToolMessage)}


def test_tool_inventory_has_no_write_shell_or_subagent_tools(bot_folder: Path) -> None:
    answer = AgentAnswer(status="not_found", answer="The documents do not say.", reason="Not in the documents.")
    model = ScriptedChatModel(script=[tool_call("search_documents", {"query": "option b"}), final_answer(answer)])
    _run(model, bot_folder)

    assert len(model.offered_tools) == 2
    for offered in model.offered_tools:
        assert set(offered) == {"search_documents", "read_file", "AgentAnswer"}
        assert not FORBIDDEN & set(offered)


def test_skill_is_listed_and_readable_but_nothing_else_is(bot_folder: Path) -> None:
    outside_absolute = str(bot_folder / "secret.txt")
    answer = AgentAnswer(status="not_found", answer="The documents do not say.", reason="Not in the documents.")
    model = ScriptedChatModel(
        script=[
            tool_call("read_file", {"file_path": "/skills/table-lookup/SKILL.md"}, "skill"),
            tool_call("read_file", {"file_path": "/skills/../secret.txt"}, "traversal"),
            tool_call("read_file", {"file_path": outside_absolute}, "absolute"),
            tool_call("read_file", {"file_path": "/secret.txt"}, "outside_route"),
            tool_call("write_file", {"file_path": "/skills/new.md", "content": "x"}, "write"),
            tool_call("task", {"description": "do it", "subagent_type": "general-purpose"}, "task"),
            final_answer(answer),
        ]
    )
    result = _run(model, bot_folder)
    outputs = _tool_results(result)

    system_prompt = str(model.calls[0][0].content)
    assert "table-lookup" in system_prompt and "/skills/table-lookup/SKILL.md" in system_prompt
    assert SKILL_BODY in outputs["skill"]
    for blocked in ("traversal", "absolute", "outside_route"):
        assert SECRET not in outputs[blocked]
        assert outputs[blocked].startswith("Error")
    assert outputs["write"] == "Error: tool write_file is not available."
    assert outputs["task"] == "Error: tool task is not available."
    assert sorted(p.name for p in (bot_folder / "skills").rglob("*")) == ["SKILL.md", "table-lookup"]


def test_read_only_backend_refuses_writes_even_when_called_directly(bot_folder: Path) -> None:
    from spikes.readonly_agent import ReadOnlyFilesystemBackend

    backend = ReadOnlyFilesystemBackend(root_dir=bot_folder / "skills", virtual_mode=True)
    assert backend.write("/x.md", "x").error
    assert backend.edit("/table-lookup/SKILL.md", "Read", "Skip").error
    assert backend.delete("/table-lookup/SKILL.md").error
    assert backend.upload_files([("/y.md", b"y")])[0].error
    assert SKILL_BODY in (bot_folder / "skills" / "table-lookup" / "SKILL.md").read_text(encoding="utf-8")


def test_structured_answer_round_trips_with_row_citation(bot_folder: Path) -> None:
    expected = AgentAnswer(
        status="answered",
        answer="Under Option C the benefit is R37 500 a month.",
        citations=[
            CitationOut(label="c1", quote="75% of monthly salary", supports="the Option C replacement ratio"),
            CitationOut(label="c7.r4", quote="Option C: 75%", supports="the table row relied on"),
        ],
        calculations=[CalculationOut(expression="0.75 * 50000", result="37500.00", rule="c1")],
        caveats=["Subject to the scheme maximum."],
    )
    model = ScriptedChatModel(script=[tool_call("search_documents", {"query": "option c"}), final_answer(expected)])
    result = _run(model, bot_folder)

    assert result["structured_response"] == expected
    assert result["structured_response"].citations[1].label == "c7.r4"
    assert isinstance(result["messages"][-1], (AIMessage, ToolMessage))


def test_malformed_structured_answer_is_retried(bot_folder: Path) -> None:
    good = AgentAnswer(status="not_found", answer="The documents do not say.", reason="Not covered.")
    bad = tool_call("AgentAnswer", {"status": "maybe", "answer": "?"}, "bad")
    model = ScriptedChatModel(script=[bad, final_answer(good)])
    result = _run(model, bot_folder)

    assert result["structured_response"] == good
    retry_feedback = _tool_results(result)["bad"]
    assert "error" in retry_feedback.lower()
