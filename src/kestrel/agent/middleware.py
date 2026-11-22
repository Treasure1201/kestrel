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
