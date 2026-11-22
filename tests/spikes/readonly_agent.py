"""The read-only DeepAgents construction proved by the spikes (the agent package builds on it).

How the built-in tools are removed, on deepagents 0.7.41:

1. `FilesystemMiddleware(tools=["read_file"])` passed in `middleware=` replaces the
   default filesystem middleware by name, so `ls`, `write_file`, `edit_file`,
   `delete`, `glob`, `grep` and `execute` are never created.
2. The default general-purpose sub-agent (`task` tool) is added by `create_deep_agent`
   unless a globally registered harness profile disables it. Rather than depend on
   process-wide registration matched by provider name, `ToolAllowList` filters the tools
   offered on every model call to an explicit set, and rejects a call to any other tool
   name before it runs. It fails safe: a tool added by a future version is not offered.

How skills stay read-only:

- The backend is a `CompositeBackend`: `/skills/` routes to `ReadOnlyFilesystemBackend`
  rooted at the bot's skills folder (virtual mode, so `..` and absolute host paths cannot
  escape it), and everything else goes to the in-memory `StateBackend`, so framework
  writes (conversation summaries) never touch the disk.
- `ReadOnlyFilesystemBackend` refuses every write, edit, delete and upload.
- `permissions` deny every write, and deny reads outside `/skills/`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from deepagents import FilesystemMiddleware, FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from kestrel.contracts import AgentAnswer

SKILLS_ROUTE = "/skills/"
_READ_ONLY = "permission_denied"


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """A filesystem backend that can list and read but never change anything."""

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=f"{file_path}: read-only")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error=f"{file_path}: read-only")

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=f"{file_path}: read-only")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=_READ_ONLY) for path, _ in files]


class ToolAllowList(AgentMiddleware[Any, Any, Any]):
    """Offer the model only the named tools, and refuse calls to any other tool."""

    def __init__(self, allowed: Sequence[str]) -> None:
        super().__init__()
        self.allowed = frozenset(allowed)

    def _filter(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        tools = [t for t in request.tools if (t.get("name") if isinstance(t, dict) else t.name) in self.allowed]
        return request.override(tools=tools)

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse[Any]]
    ) -> ModelResponse[Any]:
        return handler(self._filter(request))

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]
    ) -> ModelResponse[Any]:
        return await handler(self._filter(request))

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call["name"]
        if name in self.allowed:
            return None
        return ToolMessage(content=f"Error: tool {name} is not available.", tool_call_id=request.tool_call["id"] or "", name=name, status="error")

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]]
    ) -> ToolMessage | Command[Any]:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]
    ) -> ToolMessage | Command[Any]:
        return self._refusal(request) or await handler(request)


def build_read_only_agent(model: BaseChatModel, tools: Sequence[BaseTool], skills_dir: Path, system_prompt: str) -> Any:
    """Build a deep agent whose only abilities are `tools`, reading skills, and answering."""
    skills = ReadOnlyFilesystemBackend(root_dir=skills_dir, virtual_mode=True)
    backend = CompositeBackend(default=StateBackend(), routes={SKILLS_ROUTE: skills})
    allowed = [tool.name for tool in tools] + ["read_file"]
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        backend=backend,
        skills=[SKILLS_ROUTE],
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["read"], paths=[f"{SKILLS_ROUTE}**"], mode="allow"),
            FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
        ],
        middleware=[
            FilesystemMiddleware(backend=backend, tools=["read_file"], tool_token_limit_before_evict=None),
            ToolAllowList(allowed),
        ],
        response_format=ToolStrategy(AgentAnswer),
        name="read-only-spike",
    )
