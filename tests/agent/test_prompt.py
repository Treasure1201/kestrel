"""The core prompt carries the rules every bot relies on."""

from __future__ import annotations

from kestrel.agent.prompt import CORE_PROMPT, system_prompt


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_answer_exactly_what_was_asked() -> None:
    prompt = _flat(CORE_PROMPT)
    assert "Answer exactly what was asked." in prompt
    assert "do not volunteer figures for other options, bands, rows or products unless the question asks for a comparison" in prompt
    assert "normally one to three sentences" in prompt
    assert "put a condition attached to the rule in the answer, and leave out background" in prompt


def test_range_lookups_use_the_tool_value() -> None:
    prompt = _flat(CORE_PROMPT)
    assert "call get_table with value set to that number and use the row it names" in prompt
    assert "do not pick the row yourself" in prompt


def test_bot_guidance_follows_the_core_prompt() -> None:
    assert system_prompt("Be brief.").endswith("Guidance for this assistant\nBe brief.")
