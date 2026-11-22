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

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents import FilesystemMiddleware, FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from kestrel.agent.readonly import ReadOnlyFilesystemBackend, ToolAllowList
from kestrel.contracts import AgentAnswer

__all__ = ["ReadOnlyFilesystemBackend", "ToolAllowList", "build_read_only_agent"]

SKILLS_ROUTE = "/skills/"


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
