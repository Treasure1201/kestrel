"""Read-only building blocks for the agent, verified on deepagents 0.7.41.

How the framework's built-in tools are removed:

1. `FilesystemMiddleware(tools=["read_file"])` passed in `middleware=` replaces the
   default filesystem middleware by name, so `ls`, `write_file`, `edit_file`, `delete`,
   `glob`, `grep` and `execute` are never created.
2. `create_deep_agent` still adds the general-purpose sub-agent (`task`) and the to-do
   tool. Rather than depend on process-wide registration, `ToolAllowList` filters the
   tools offered on every model call to an explicit set, and refuses a call to any
   other tool name before it runs. It fails safe: a tool added by a future version is
   not offered.

How skills stay read-only:

- The backend is a `CompositeBackend`: each skills route maps to a
  `ReadOnlyFilesystemBackend` rooted at one skills folder (virtual mode, so `..` and
  absolute host paths cannot escape it); everything else goes to the in-memory
  `StateBackend`, so framework writes never touch the disk.
- `ReadOnlyFilesystemBackend` refuses every write, edit, delete and upload.
- Permissions deny every write, and deny reads outside the skills routes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

SKILLS_ROOT = "/skills/"
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


def _tool_name(tool: Any) -> str:
    return str(tool.get("name") if isinstance(tool, dict) else tool.name)


class ToolAllowList(AgentMiddleware[Any, Any, Any]):
    """Offer the model only the named tools, and refuse calls to any other tool.

    The structured-answer tool is added by the agent runtime after this filter, so it
    is always offered.
    """

    def __init__(self, allowed: Sequence[str]) -> None:
        super().__init__()
        self.allowed = frozenset(allowed)

    def _filter(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        return request.override(tools=[t for t in request.tools if _tool_name(t) in self.allowed])

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
        return ToolMessage(
            content=f"Error: tool {name} is not available.",
            tool_call_id=request.tool_call["id"] or "",
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]]
    ) -> ToolMessage | Command[Any]:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]
    ) -> ToolMessage | Command[Any]:
        return self._refusal(request) or await handler(request)


def skills_backend(folders: Sequence[Path]) -> tuple[Any, list[str], list[FilesystemPermission]]:
    """Backend, skill source routes and permissions for read-only skills.

    With no folders the backend is in-memory only, there are no skill routes, and
    every read and write is denied.
    """
    routes: dict[str, Any] = {}
    for index, folder in enumerate(folders):
        route = f"{SKILLS_ROOT}{index}-{folder.name}/" if len(folders) > 1 else SKILLS_ROOT
        routes[route] = ReadOnlyFilesystemBackend(root_dir=folder, virtual_mode=True)
    backend = CompositeBackend(default=StateBackend(), routes=routes)
    permissions = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    permissions += [FilesystemPermission(operations=["read"], paths=[f"{route}**"], mode="allow") for route in routes]
    permissions.append(FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"))
    return backend, list(routes), permissions
