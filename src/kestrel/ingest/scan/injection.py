"""Flag wording that reads as instructions aimed at an AI assistant rather than at a person.

The rules are a small, explainable first layer. They match how injected instructions
are phrased (who they address, what they try to override, what they ask the assistant
to say), never a particular document. Strong rules flag on their own; weak rules,
which also occur in ordinary business writing ("tell the customer"), flag only in
combination with another rule.

A flag does not remove visible text: what a reader can see stays searchable, and the
finding tells an operator to review it. Hidden text is removed by the visibility rules
regardless of what it says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Plain "agents" and "models" are left out on purpose: call-centre agents are people.
_AUDIENCE = (
    r"(?:ai (?:assistants?|agents?|models?|systems?|tools?)|ai|a\.i\.|llms?|large language models?|language models?|"
    r"assistants?|chat ?bots?|bots?|automated (?:systems?|agents?|tools?|assistants?))"
)

# (name, strong, pattern)
_RULES: tuple[tuple[str, bool, re.Pattern[str]], ...] = (
    (
        "addressed_to_ai",
        True,
        re.compile(
            rf"\b(?:note|message|instructions?|attention|notice|reminder)\s+(?:to|for)\s+(?:all\s+|any\s+)?{_AUDIENCE}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "override_instructions",
        True,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[^.!?\n]{0,40}?"
            r"\b(?:previous|prior|above|earlier|preceding|all|any|your|these|those|other)\b[^.!?\n]{0,30}?"
            r"\b(?:instructions?|prompts?|rules|guidelines|directions|directives|context|guardrails)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_terms",
        True,
        re.compile(r"\b(?:system prompt|developer (?:message|mode)|jailbreak|do anything now|prompt injection)\b", re.IGNORECASE),
    ),
    (
        "role_markup",
        True,
        re.compile(r"<\s*/?\s*(?:system|assistant|instructions?)\s*>|\[\s*/?\s*(?:system|inst)\s*\]", re.IGNORECASE),
    ),
    (
        "directive_to_ai",
        False,
        re.compile(
            rf"\b(?:if you are|as an?|you,? the)\s+{_AUDIENCE}\b"
            rf"|\b{_AUDIENCE}\s+(?:must|should|shall|are instructed to|will now)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "dictates_reply",
        False,
        re.compile(
            r"\b(?:tell|inform|promise|convince|answer|respond to|reply to)\s+(?:the\s+|any\s+|every\s+)?"
            r"(?:user|customer|caller|reader|person asking|requester)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_reassignment",
        False,
        re.compile(
            r"\b(?:you are now|from now on,? you|pretend (?:to be|you are)|roleplay as|act as (?:an?|the) )", re.IGNORECASE
        ),
    ),
    (
        "concealment",
        False,
        re.compile(
            r"\b(?:do not|don't|never)\s+(?:mention|reveal|disclose|repeat|show)\b[^.!?\n]{0,40}"
            r"\b(?:instructions?|this note|this message|prompt)\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class InstructionMatch:
    """Why a passage was flagged: the rules that matched and the matching phrases."""

    rules: tuple[str, ...]
    evidence: tuple[str, ...]


def detect_instructions(text: str) -> InstructionMatch | None:
    """Return the match when `text` reads as instructions to an AI assistant, else None."""
    names: list[str] = []
    evidence: list[str] = []
    strong = False
    for name, is_strong, pattern in _RULES:
        found = pattern.search(text)
        if found:
            names.append(name)
            evidence.append(found.group(0).strip())
            strong = strong or is_strong
    if strong or len(names) >= 2:
        return InstructionMatch(rules=tuple(names), evidence=tuple(evidence))
    return None
