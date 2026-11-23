"""On-device model traffic stays on the device: no environment proxy for loopback Ollama."""

from __future__ import annotations

import pytest

from kestrel.config import Settings
from kestrel.llm import resolve_model


@pytest.fixture
def proxied(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.example.invalid:3128")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")


@pytest.mark.usefixtures("proxied")
def test_loopback_ollama_ignores_environment_proxies(settings: Settings) -> None:
    chat = resolve_model("ollama:gpt-oss:20b", settings).chat_model
    for wrapper in (chat._client, chat._async_client):
        client = wrapper._client
        assert client.trust_env is False
        assert not any(transport is not None for transport in client._mounts.values())


@pytest.mark.usefixtures("proxied")
def test_remote_ollama_keeps_the_environment_proxy(settings: Settings) -> None:
    remote = settings.model_copy(update={"ollama_base_url": "http://gpu-box.internal:11434"})
    chat = resolve_model("ollama:gpt-oss:20b", remote).chat_model
    assert chat._client._client.trust_env is True
