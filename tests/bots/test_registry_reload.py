"""The registry reloads a bot when its bot.yaml, prompt, manifest or a config file changes."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml

from kestrel.bots import BotRegistry
from kestrel.config import Settings

MCP = """
servers:
  - name: lookup
    transport: stdio
    command: python
    args: ["server.py"]
    allowed_tools: [find]
    locality: on-device
    read_only: {reviewed_by: tester, reviewed_on: "2026-11-22", statement: "find only reads"}
"""


def _bot(**overrides: object) -> dict:
    data = {
        "id": "demo", "name": "Demo bot", "audience": ["staff"],
        "knowledge": {"collections": ["demo"], "approval": "required"},
        "prompt": "prompt.md", "tools": ["search_documents"], "model": {"default": "ollama:gpt-oss:20b"},
    }
    data.update(overrides)
    return data


def _touch(path: Path, text: str) -> None:
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))  # a later time even on coarse clocks


def _layout(tmp_path: Path, settings: Settings) -> tuple[Path, Settings]:
    root = tmp_path / "bots" / "demo"
    root.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    (config / "mcp_servers.yaml").write_text(MCP, encoding="utf-8")
    (root / "bot.yaml").write_text(yaml.safe_dump(_bot()), encoding="utf-8")
    (root / "prompt.md").write_text("Be brief.", encoding="utf-8")
    (root / "manifest.yaml").write_text("collection: demo\ndocuments: []\n", encoding="utf-8")
    return root, settings.model_copy(update={"bots_dir": tmp_path / "bots", "config_dir": config})


def test_unchanged_files_return_the_cached_bot(tmp_path: Path, settings: Settings) -> None:
    _, configured = _layout(tmp_path, settings)
    registry = BotRegistry(configured)
    assert registry.get("demo") is registry.get("demo")


def test_edited_bot_yaml_and_prompt_are_reloaded(tmp_path: Path, settings: Settings) -> None:
    root, configured = _layout(tmp_path, settings)
    registry = BotRegistry(configured)
    assert registry.get("demo").name == "Demo bot"
    _touch(root / "bot.yaml", yaml.safe_dump(_bot(name="Renamed bot")))
    assert registry.get("demo").name == "Renamed bot"
    _touch(root / "prompt.md", "Be very brief.")
    assert registry.get("demo").prompt_text == "Be very brief."


def test_manifest_and_config_changes_reload(tmp_path: Path, settings: Settings) -> None:
    root, configured = _layout(tmp_path, settings)
    registry = BotRegistry(configured)
    first = registry.get("demo")
    _touch(root / "manifest.yaml", "collection: demo\ndocuments: []\n# edited\n")
    second = registry.get("demo")
    assert second is not first
    _touch(Path(configured.config_dir) / "mcp_servers.yaml", MCP)
    third = registry.get("demo")
    assert third is not second
    _touch(root / "bot.yaml", yaml.safe_dump(_bot(mcp_servers=["lookup"])))
    assert [s.name for s in registry.mcp_specs(registry.get("demo"))] == ["lookup"]


def test_concurrent_access_is_safe(tmp_path: Path, settings: Settings) -> None:
    _, configured = _layout(tmp_path, settings)
    registry = BotRegistry(configured)
    names: list[str] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            for _ in range(20):
                names.append(registry.get("demo").name)
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and set(names) == {"Demo bot"}
