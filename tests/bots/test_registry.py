"""Bot registry: the product bot, env expansion, path safety, field-named validation errors, visibility."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from kestrel.bots import BotRegistry, load_mcp_servers
from kestrel.config import Settings
from kestrel.contracts import Principal
from kestrel.errors import ConfigurationError, InvalidInputError
from kestrel.llm import check_egress

MCP_FILE = """
servers:
  - name: lookup
    transport: stdio
    command: python
    args: ["server.py"]
    allowed_tools: [find]
    locality: on-device
    read_only: {reviewed_by: tester, reviewed_on: "2026-11-22", statement: "find only reads"}
  - name: remote-search
    transport: http
    url: ${KESTREL_TEST_MCP_URL:-https://search.example.invalid/mcp}
    allowed_tools: [search]
    locality: external
    read_only: {reviewed_by: tester, reviewed_on: "2026-11-22", statement: "search only reads"}
"""


def base_bot(bot_id: str = "demo", **overrides: object) -> dict:
    bot = {
        "id": bot_id,
        "name": "Demo bot",
        "audience": ["staff"],
        "knowledge": {"collections": [bot_id], "approval": "none"},
        "prompt": "prompt.md",
        "tools": ["search_documents"],
        "model": {"default": "ollama:gpt-oss:20b"},
    }
    bot.update(overrides)
    return bot


@pytest.fixture
def layout(tmp_path: Path, settings: Settings) -> tuple[Path, Settings]:
    bots = tmp_path / "bots"
    config = tmp_path / "config"
    bots.mkdir()
    config.mkdir()
    (config / "mcp_servers.yaml").write_text(MCP_FILE, encoding="utf-8")
    return bots, settings.model_copy(update={"bots_dir": bots, "config_dir": config})


def write_bot(bots: Path, data: dict, prompt: str | None = "Be brief.", folder: str | None = None) -> Path:
    root = bots / (folder or data["id"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "bot.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if prompt is not None:
        (root / "prompt.md").write_text(prompt, encoding="utf-8")
    return root


def load_error(layout: tuple[Path, Settings], data: dict, **kwargs: object) -> str:
    bots, settings = layout
    write_bot(bots, data, **kwargs)
    with pytest.raises(ConfigurationError) as info:
        BotRegistry(settings).get(data["id"])
    return info.value.user_message


def test_product_bot_loads(settings: Settings, repo_root: Path) -> None:
    bot = BotRegistry(settings).get("kestrel-products")
    root = repo_root / "bots" / "kestrel-products"
    assert bot.knowledge.collections == ("kestrel-products",)
    assert bot.knowledge.approval == "required" and bot.knowledge.manifest == root / "manifest.yaml"
    assert bot.audience == frozenset({"call-centre-agent", "broker"})
    assert bot.tools == ("search_documents", "get_table", "calculate")
    assert bot.mcp_servers == () and bot.skills == ()
    assert bot.privacy.classification == "internal" and bot.privacy.on_personal_information == "redact"
    assert bot.verification.on_failure == "withhold"
    assert bot.model.default == settings.model
    assert bot.prompt_path == root / "prompt.md" and "call-centre agents" in bot.prompt_text
    assert BotRegistry(settings).mcp_specs(bot) == ()
    check_egress(bot, bot.model.default, (), settings.model_copy(update={"model": "ollama:gpt-oss:20b"}))


def test_env_expansion_with_default_and_settings(layout: tuple[Path, Settings], monkeypatch: pytest.MonkeyPatch) -> None:
    bots, settings = layout
    monkeypatch.setenv("DEMO_BOT_NAME", "From env")
    data = base_bot(
        name="${DEMO_BOT_NAME}",
        description="${DEMO_UNSET:-fallback text}",
        model={"default": "${KESTREL_MODEL}"},
    )
    write_bot(bots, data)
    bot = BotRegistry(settings.model_copy(update={"model": "openai:gpt-6.1-sol"})).get("demo")
    assert bot.name == "From env" and bot.description == "fallback text"
    assert bot.model.default == "openai:gpt-6.1-sol"


def test_undefined_variable_and_secret_reference_fail(layout: tuple[Path, Settings]) -> None:
    assert "field `name` uses ${DEMO_MISSING}" in load_error(layout, base_bot(name="${DEMO_MISSING}"))
    assert "secrets cannot be used" in load_error(layout, base_bot(description="${ANTHROPIC_API_KEY}"))


@pytest.mark.parametrize("bad", ["../outside.md", "/etc/passwd", "C:\\Windows\\win.ini", "sub/../../x.md"])
def test_prompt_path_escape_is_rejected(layout: tuple[Path, Settings], bad: str) -> None:
    message = load_error(layout, base_bot(prompt=bad))
    assert "field `prompt`" in message


def test_skill_path_escape_and_missing_folder(layout: tuple[Path, Settings]) -> None:
    assert "field `skills[0]`" in load_error(layout, base_bot(skills=["../other-bot/skills"]))
    assert "does not exist" in load_error(layout, base_bot(skills=["skills"]))


def test_skill_folder_inside_bot_is_resolved(layout: tuple[Path, Settings]) -> None:
    bots, settings = layout
    root = write_bot(bots, base_bot(skills=["skills"]))
    (root / "skills").mkdir()
    assert BotRegistry(settings).get("demo").skills == ((root / "skills").resolve(),)


def test_unknown_tool_names_the_field(layout: tuple[Path, Settings]) -> None:
    message = load_error(layout, base_bot(tools=["search_documents", "shell"]))
    assert "bots/demo/bot.yaml: field `tools`" in message and "shell" in message


def test_unknown_mcp_server_names_the_field(layout: tuple[Path, Settings]) -> None:
    message = load_error(layout, base_bot(mcp_servers=["nope"]))
    assert "field `mcp_servers`" in message and "lookup" in message


def test_missing_prompt_names_the_field(layout: tuple[Path, Settings]) -> None:
    assert "field `prompt`" in load_error(layout, base_bot(), prompt=None)
    data = base_bot()
    del data["prompt"]
    assert "field `prompt`: is required" in load_error(layout, data)
    assert "is empty" in load_error(layout, base_bot(), prompt="   ")


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"privacy": {"classification": "secret"}}, "privacy.classification"),
        ({"privacy": {"on_personal_information": "ignore"}}, "privacy.on_personal_information"),
        ({"verification": {"on_failure": "shrug"}}, "verification.on_failure"),
        ({"knowledge": {"collections": ["demo"], "approval": "maybe"}}, "knowledge.approval"),
        ({"model": {"default": "ollama:gpt-oss:20b", "allowed_providers": ["azure"]}}, "model.allowed_providers.0"),
        ({"model": {"default": "gpt-oss"}}, "model.default"),
        ({"audience": []}, "audience"),
        ({"colour": "blue"}, "colour"),
    ],
)
def test_bad_values_name_the_field(layout: tuple[Path, Settings], override: dict, field: str) -> None:
    message = load_error(layout, base_bot(**override))
    assert f"field `{field}`" in message and "bots/demo/bot.yaml" in message


def test_id_must_match_folder(layout: tuple[Path, Settings]) -> None:
    bots, settings = layout
    write_bot(bots, base_bot("other"), folder="demo")
    with pytest.raises(ConfigurationError, match="field `id`"):
        BotRegistry(settings).get("demo")


def test_unknown_bot_lists_available(layout: tuple[Path, Settings]) -> None:
    bots, settings = layout
    write_bot(bots, base_bot("alpha"))
    write_bot(bots, base_bot("beta"))
    with pytest.raises(InvalidInputError, match="Available bots: alpha, beta"):
        BotRegistry(settings).get("gamma")


def test_role_visibility(layout: tuple[Path, Settings]) -> None:
    bots, settings = layout
    write_bot(bots, base_bot("staff-only", audience=["staff"]))
    write_bot(bots, base_bot("brokers", audience=["broker", "call-centre-agent"]))
    registry = BotRegistry(settings)
    assert [b.id for b in registry.list()] == ["brokers", "staff-only"]
    broker = Principal(user="b", roles=frozenset({"broker"}), channel="cli")
    nobody = Principal(user="n", roles=frozenset(), channel="cli")
    assert [b.id for b in registry.visible_to(broker)] == ["brokers"]
    assert registry.visible_to(nobody) == []


def test_mcp_specs_and_python_resolution(layout: tuple[Path, Settings]) -> None:
    bots, settings = layout
    write_bot(bots, base_bot(mcp_servers=["remote-search", "lookup"]))
    registry = BotRegistry(settings)
    specs = registry.mcp_specs(registry.get("demo"))
    assert [s.name for s in specs] == ["remote-search", "lookup"]
    assert specs[0].url == "https://search.example.invalid/mcp" and specs[0].locality == "external"
    assert specs[1].command == sys.executable


def test_invalid_mcp_file_names_the_entry_and_field(tmp_path: Path) -> None:
    (tmp_path / "mcp_servers.yaml").write_text("servers:\n  - name: x\n    transport: stdio\n    allowed_tools: [a]\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"servers\[0\].*field `locality`"):
        load_mcp_servers(tmp_path)


def test_repository_mcp_file_defines_only_the_workday_calendar(repo_root: Path) -> None:
    servers = load_mcp_servers(repo_root / "config")
    assert list(servers) == ["workday-calendar"]
    spec = servers["workday-calendar"]
    assert spec.transport == "stdio" and spec.locality == "on-device"
    assert spec.allowed_tools == ("today", "working_days_between")
