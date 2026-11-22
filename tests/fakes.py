"""Offline stand-ins for the chat model and the embedder."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from kestrel.contracts import AgentAnswer


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed list of AI messages, one per model call, and records what it was sent.

    Supports `bind_tools`, so the real agent runtime (tool calling and tool-call based
    structured output) runs unchanged. After a run, `calls` holds the messages of each
    call and `offered_tools` the tool names bound for each call, which is how tests
    assert the tool inventory the model actually saw.
    """

    script: list[AIMessage]
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    offered_tools: list[list[str]] = Field(default_factory=list)
    pending_tools: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """Record the tool names; the scripted replies decide which tools get called."""
        self.pending_tools = [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        index = len(self.calls)
        self.calls.append(list(messages))
        self.offered_tools.append(list(self.pending_tools))
        if index >= len(self.script):
            raise AssertionError(f"scripted model called {index + 1} times but only {len(self.script)} replies scripted")
        reply = self.script[index].model_copy(deep=True)
        return ChatResult(generations=[ChatGeneration(message=reply)])


def tool_call(name: str, args: dict[str, Any], call_id: str | None = None) -> AIMessage:
    """An AI message that calls one tool."""
    return AIMessage(content="", tool_calls=[ToolCall(name=name, args=args, id=call_id or f"call_{name}")])


def final_answer(answer: AgentAnswer, call_id: str = "call_final") -> AIMessage:
    """An AI message that returns `answer` through the tool-call structured output strategy."""
    return tool_call(type(answer).__name__, answer.model_dump(mode="json"), call_id)


_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic bag-of-words embeddings: texts sharing words get higher cosine similarity.

    Each lower-cased word is hashed to one of `dimension` buckets with a sign; the
    vector is L2-normalised. Text with no words maps to a fixed unit vector so the
    result is never the zero vector (cosine distance is undefined for it).
    """

    def __init__(self, dimension: int = 64) -> None:
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return f"fake-bow-{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dimension
            vector[bucket] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return [1.0] + [0.0] * (self._dimension - 1)
        return [v / norm for v in vector]
