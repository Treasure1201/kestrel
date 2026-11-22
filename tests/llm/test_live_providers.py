"""One-word smoke test per provider. Run with `uv run pytest -m live tests/llm`.

Keys come from the environment or the repository's `.env`; a provider without a key
(or a local Ollama that is not running) is skipped. Keys are never printed.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from kestrel.config import Settings
from kestrel.llm import check_model, resolve_model, usage_from_messages

pytestmark = pytest.mark.live

MODELS = {
    "ollama": "ollama:gpt-oss:20b",
    "ollama-cloud": "ollama-cloud:gpt-oss:120b",
    "anthropic": "anthropic:claude-haiku-5-5",
    "openai": "openai:gpt-6.1-sol",
}


@pytest.mark.parametrize("provider", list(MODELS))
def test_provider_answers_one_word(provider: str) -> None:
    settings = Settings()
    ref = MODELS[provider]
    readiness = check_model(ref, settings)
    if readiness.status == "fail":
        pytest.skip(f"{ref} not ready: {readiness.detail}")
    resolved = resolve_model(ref, settings)
    reply = resolved.chat_model.invoke([HumanMessage("Reply with exactly one word: ready")])
    assert reply.text.strip()
    usage = usage_from_messages([reply])
    assert usage.model_calls == 1 and usage.output_tokens > 0
