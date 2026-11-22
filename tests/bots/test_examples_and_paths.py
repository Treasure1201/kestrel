"""Example questions in bot.yaml, and MCP script paths that work from any working directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

from kestrel.bots import BotRegistry, load_mcp_servers
from kestrel.config import Settings
from kestrel.contracts import MAX_EXAMPLES, Principal
from kestrel.errors import ConfigurationError


def test_product_bot_lists_example_questions(settings: Settings) -> None:
    bot = BotRegistry(settings).get("kestrel-products")
    assert len(bot.examples) == 4
    assert all(question.endswith("?") and len(question) <= 120 for question in bot.examples)


def test_examples_reach_the_bot_summary(settings: Settings) -> None:
    from fakes import FakeEmbedder

    from kestrel.service import build_service

    service = build_service(settings, embedder=FakeEmbedder())
    try:
        who = Principal(user="u", roles=frozenset({"broker"}), channel="cli")
        (summary,) = [b for b in service.list_bots(who) if b.id == "kestrel-products"]
        assert summary.examples == BotRegistry(settings).get("kestrel-products").examples
    finally:
        service.close()


def _bot_with_examples(tmp_path: Path, settings: Settings, examples: object) -> Settings:
    bots = tmp_path / "bots"
    root = bots / "demo"
    root.mkdir(parents=True)
    (root / "prompt.md").write_text("Be brief.", encoding="utf-8")
    data = {"id": "demo", "name": "Demo", "audience": ["staff"], "knowledge": {"collections": ["demo"], "approval": "none"},
            "prompt": "prompt.md", "model": {"default": "ollama:gpt-oss:20b"}, "examples": examples}
    (root / "bot.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    return settings.model_copy(update={"bots_dir": bots, "config_dir": config})


def test_examples_are_optional_trimmed_and_limited(tmp_path: Path, settings: Settings) -> None:
    ok = _bot_with_examples(tmp_path / "a", settings, ["  What is   covered? "])
    assert BotRegistry(ok).get("demo").examples == ("What is covered?",)
    too_many = _bot_with_examples(tmp_path / "b", settings, [f"Question {n}?" for n in range(MAX_EXAMPLES + 1)])
    with pytest.raises(ConfigurationError, match="examples"):
        BotRegistry(too_many).get("demo")
    too_long = _bot_with_examples(tmp_path / "c", settings, ["x" * 121])
    with pytest.raises(ConfigurationError, match="examples"):
        BotRegistry(too_long).get("demo")


def test_relative_mcp_script_is_resolved_against_the_repository_root(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # not the repository root
    spec = load_mcp_servers(repo_root / "config")["workday-calendar"]
    script = Path(spec.args[0])
    assert script.is_absolute() and script.is_file()
    assert script == (repo_root / "examples" / "mcp_servers" / "workday_calendar.py").resolve()
    assert spec.command == sys.executable


def test_only_existing_files_inside_the_root_are_resolved(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "tool.py").write_text("print('x')\n", encoding="utf-8")
    outside = tmp_path.parent / f"outside-{os.getpid()}.py"
    (config / "mcp_servers.yaml").write_text(
        "servers:\n  - name: local\n    transport: stdio\n    command: python\n"
        f"    args: [tool.py, --flag, missing.py, '../{outside.name}', '42']\n"
        "    allowed_tools: [a]\n    locality: on-device\n"
        "    read_only: {reviewed_by: t, reviewed_on: '2026-11-23', statement: reads}\n",
        encoding="utf-8",
    )
    outside.write_text("", encoding="utf-8")
    try:
        args = load_mcp_servers(config)["local"].args
    finally:
        outside.unlink()
    assert args[0] == str((tmp_path / "tool.py").resolve())
    assert args[1:] == ("--flag", "missing.py", f"../{outside.name}", "42")
