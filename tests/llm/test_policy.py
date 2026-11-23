"""Egress policy and tracing switches."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kestrel.config import Settings
from kestrel.contracts import BotConfig, KnowledgeConfig, McpServerSpec, ModelPolicy, PrivacyPolicy, ReadOnlyAttestation
from kestrel.errors import EgressDeniedError
from kestrel.llm import check_egress, disable_third_party_tracing, is_loopback_url, model_locality


def make_bot(tmp_path: Path, classification: str = "internal", allowed: tuple[str, ...] | None = None) -> BotConfig:
    policy = ModelPolicy(default="ollama:gpt-oss:20b", **({"allowed_providers": allowed} if allowed else {}))
    return BotConfig(
        id="demo",
        name="Demo",
        audience=frozenset({"staff"}),
        knowledge=KnowledgeConfig(collections=("demo",), approval="none"),
        prompt_path=tmp_path / "prompt.md",
        prompt_text="Be brief.",
        model=policy,
        privacy=PrivacyPolicy(classification=classification),
        root=tmp_path,
    )


def mcp(locality: str) -> McpServerSpec:
    return McpServerSpec(
        name=f"tool-{locality}",
        transport="http",
        url="https://tools.example.invalid/mcp",
        allowed_tools=("lookup",),
        locality=locality,
        read_only=ReadOnlyAttestation(reviewed_by="r", reviewed_on="2026-11-22", statement="reads only"),
    )


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://127.5.0.1:11434", True),
        ("http://localhost:11434", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.20:11434", False),
        ("https://ollama.com", False),
        ("http://127.0.0.1.example.invalid", False),
    ],
)
def test_loopback_detection(url: str, loopback: bool) -> None:
    assert is_loopback_url(url) is loopback


def test_provider_not_in_allowed_list_is_denied(tmp_path: Path, settings: Settings) -> None:
    bot = make_bot(tmp_path, allowed=("ollama", "anthropic"))
    with pytest.raises(EgressDeniedError) as info:
        check_egress(bot, "openai:gpt-6.1-sol", (), settings)
    assert "allowed_providers" in info.value.user_message and "ollama, anthropic" in info.value.user_message
    assert check_egress(bot, "anthropic:claude-haiku-5-5", (), settings)[0].locality == "external"


def test_confidential_bot_refuses_an_external_model(tmp_path: Path, settings: Settings) -> None:
    bot = make_bot(tmp_path, classification="confidential")
    for ref in ("anthropic:claude-sonnet-5-5", "ollama-cloud:gpt-oss:120b", "openai:gpt-6.1-sol"):
        with pytest.raises(EgressDeniedError, match="KESTREL_MODEL=ollama"):
            check_egress(bot, ref, (), settings)


def test_confidential_bot_allows_local_ollama_on_loopback(tmp_path: Path, settings: Settings) -> None:
    bot = make_bot(tmp_path, classification="confidential")
    checked = check_egress(bot, "ollama:gpt-oss:20b", (mcp("on-device"),), settings)
    assert [d.locality for d in checked] == ["on-device", "on-device"]


def test_ollama_on_a_remote_host_is_external_for_confidential_bots(tmp_path: Path, settings: Settings) -> None:
    remote = settings.model_copy(update={"ollama_base_url": "http://10.0.0.5:11434"})
    assert model_locality("ollama:gpt-oss:20b", remote) == "external"
    with pytest.raises(EgressDeniedError):
        check_egress(make_bot(tmp_path, classification="confidential"), "ollama:gpt-oss:20b", (), remote)
    assert check_egress(make_bot(tmp_path), "ollama:gpt-oss:20b", (), remote)[0].locality == "external"


def test_external_mcp_server_denied_for_confidential_bot(tmp_path: Path, settings: Settings) -> None:
    with pytest.raises(EgressDeniedError, match="MCP server 'tool-external'"):
        check_egress(make_bot(tmp_path, classification="confidential"), "ollama:gpt-oss:20b", (mcp("external"),), settings)
    assert len(check_egress(make_bot(tmp_path), "ollama:gpt-oss:20b", (mcp("external"),), settings)) == 2


def test_disable_tracing_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from langsmith.utils import tracing_is_enabled

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    disable_third_party_tracing()
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    monkeypatch.setenv("LANGSMITH_TRACING", "true")  # global flag wins even if something re-enables the variable
    assert tracing_is_enabled() is False


@pytest.mark.parametrize("model", ["gpt-oss:120b-cloud", "deepseek-v3.1:671b-cloud", "glm-4.6:cloud", "Qwen3:CLOUD"])
def test_loopback_ollama_passing_through_to_a_hosted_model_is_external(tmp_path: Path, settings: Settings, model: str) -> None:
    assert model_locality(f"ollama:{model}", settings) == "external"
    with pytest.raises(EgressDeniedError):
        check_egress(make_bot(tmp_path, classification="confidential"), f"ollama:{model}", (), settings)


def test_hosted_model_suffixes_are_configurable(tmp_path: Path, settings: Settings) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "models.yaml").write_text('ollama:\n  hosted_model_suffixes: ["-remote"]\n', encoding="utf-8")
    custom = settings.model_copy(update={"config_dir": config})
    assert model_locality("ollama:llama3:8b-remote", custom) == "external"
    assert model_locality("ollama:gpt-oss:20b", custom) == "on-device"
    assert model_locality("ollama:cloudy-model:7b", settings) == "on-device"
