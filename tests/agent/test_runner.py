"""Recovering an answer a model wrote as JSON text instead of calling the answer tool."""

from __future__ import annotations

import json

from kestrel.agent.runner import answer_from_text


def test_json_answer_in_text_is_accepted_when_it_validates() -> None:
    payload = {"status": "not_found", "answer": "The documents do not say.", "reason": "The documents do not say.",
               "citations": [{"label": "c2", "quote": "some words", "claim": "x"}]}
    answer = answer_from_text("Here is my answer:\n```json\n" + json.dumps(payload) + "\n```")
    assert answer is not None and answer.status == "not_found"
    assert answer.citations[0].supports == ""


def test_text_without_a_valid_answer_is_rejected() -> None:
    assert answer_from_text("I think the answer is ten days.") is None
    assert answer_from_text('{"status": "maybe", "answer": "?"}') is None
    assert answer_from_text('{"answer": "no status"}') is None
    assert answer_from_text("{not json}") is None
