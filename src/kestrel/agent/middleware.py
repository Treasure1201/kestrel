"""Budget and retry middleware for the answering agent."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, ModelRetryMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from kestrel.agent.context import Budget
from kestrel.agent.prompt import BUDGET_NOTE

_TRANSIENT_NAMES = ("APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError", "OverloadedError", "ServiceUnavailableError")


def is_transient(exc: BaseException) -> bool:
    """Network failures, timeouts, rate limits and server errors are worth retrying; anything else is not."""
    if isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    return type(exc).__name__ in _TRANSIENT_NAMES


def retry_middleware(max_retries: int = 2) -> ModelRetryMiddleware:
    """Retry a model call on transient provider errors only, with exponential backoff; then raise."""
    return ModelRetryMiddleware(
        max_retries=max_retries, retry_on=is_transient, on_failure="error", initial_delay=1.0, backoff_factor=2.0, max_delay=20.0
    )


_RELAXED_MODELS: set[str] = set()
"""Models (by class and name) that rejected a forced tool choice in this process."""


def _model_key(model: Any) -> str:
    name = getattr(model, "model", None) or getattr(model, "model_name", None) or ""
    return f"{type(model).__name__}:{name}"


def rejects_forced_tool_choice(exc: BaseException) -> bool:
    """True for a provider's refusal of `tool_choice` any/required (a 400 that names tool_choice)."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 400 and "tool_choice" in str(exc)


class _AutoToolChoice:
    """Wraps a chat model so a forced tool choice is bound as `auto`; everything else is delegated."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        relaxed = None if tool_choice in ("any", "required", True) else tool_choice
        return self._model.bind_tools(tools, tool_choice=relaxed, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


class ToolChoiceFallback(AgentMiddleware[Any, Any, Any]):
    """Structured output forces a tool call (`tool_choice="any"`); some models refuse that.

    When a provider rejects it, the call is retried once with `auto`, and the model is
    remembered for the rest of the process. The answer is then still returned through
    the answer tool, or the runner's nudge asks for it.
    """

    def _maybe_relaxed(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        if _model_key(request.model) in _RELAXED_MODELS:
            return request.override(model=_AutoToolChoice(request.model))  # type: ignore[arg-type]
        return request

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse[Any]]
    ) -> ModelResponse[Any]:
        try:
            return handler(self._maybe_relaxed(request))
        except Exception as exc:
            if _model_key(request.model) in _RELAXED_MODELS or not rejects_forced_tool_choice(exc):
                raise
            _RELAXED_MODELS.add(_model_key(request.model))
            return handler(self._maybe_relaxed(request))

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]
    ) -> ModelResponse[Any]:
        try:
            return await handler(self._maybe_relaxed(request))
        except Exception as exc:
            if _model_key(request.model) in _RELAXED_MODELS or not rejects_forced_tool_choice(exc):
                raise
            _RELAXED_MODELS.add(_model_key(request.model))
            return await handler(self._maybe_relaxed(request))


_MALFORMED_TOOL_CALL = ("error parsing tool call", "failed to parse tool call")
MALFORMED_NOTE = (
    "Your last reply could not be read: it was not a valid tool call. Call exactly one tool, with valid JSON "
    "arguments only and no other text inside them. To finish, call the answer tool."
)


def is_malformed_tool_call(exc: BaseException) -> bool:
    """True when the provider could not parse the tool call the model wrote: the model's error, not an outage."""
    text = str(exc).casefold()
    return any(marker in text for marker in _MALFORMED_TOOL_CALL)


class MalformedReplyRetry(AgentMiddleware[Any, Any, Any]):
    """Ask again when the provider rejects a reply because its tool call could not be parsed.

    Some local models occasionally write free text where the tool-call arguments belong,
    and the provider raises instead of returning a message. That is a malformed reply,
    like an answer that fails validation, so the model is asked again with a short note,
    up to `max_retries` times. It sits outside `BudgetMiddleware`, so every retry is a
    counted model call; with no call left the original error is raised.
    """

    def __init__(self, budget: Budget, max_retries: int = 2) -> None:
        super().__init__()
        self.budget = budget
        self.max_retries = max_retries

    def _again(self, exc: Exception, attempt: int) -> bool:
        return is_malformed_tool_call(exc) and attempt < self.max_retries and self.budget.model_calls_left > 0

    @staticmethod
    def _noted(request: ModelRequest[Any]) -> ModelRequest[Any]:
        return request.override(messages=[*request.messages, HumanMessage(MALFORMED_NOTE)])

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse[Any]]
    ) -> ModelResponse[Any]:
        attempt, current = 0, request
        while True:
            try:
                return handler(current)
            except Exception as exc:
                if not self._again(exc, attempt):
                    raise
                attempt, current = attempt + 1, self._noted(request)

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]
    ) -> ModelResponse[Any]:
        attempt, current = 0, request
        while True:
            try:
                return await handler(current)
            except Exception as exc:
                if not self._again(exc, attempt):
                    raise
                attempt, current = attempt + 1, self._noted(request)


class BudgetMiddleware(AgentMiddleware[Any, Any, Any]):
    """Counts every model call against the question's budget.

    On the last allowed call, and once the search budget is spent, only the answer
    tool is offered and the model is told to answer now. A call beyond the budget, or
    after the wall-clock limit, raises `BudgetExceededError`.
    """

    def __init__(self, budget: Budget) -> None:
        super().__init__()
        self.budget = budget

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        self.budget.take_model_call()
        searches_spent = self.budget.searches >= self.budget.max_searches
        if self.budget.model_calls_left <= 0 or (searches_spent and self.budget.model_calls_left <= 1):
            return request.override(tools=[], messages=[*request.messages, HumanMessage(BUDGET_NOTE)])
        return request

    def _count(self, response: Any) -> Any:
        messages = getattr(response, "result", None) or []
        for message in messages:
            if isinstance(message, AIMessage) and message.usage_metadata:
                meta = message.usage_metadata
                self.budget.add_tokens(meta.get("input_tokens") or 0, meta.get("output_tokens") or 0)
        return response

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse[Any]]
    ) -> ModelResponse[Any]:
        return self._count(handler(self._prepare(request)))

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]
    ) -> ModelResponse[Any]:
        return self._count(await handler(self._prepare(request)))
