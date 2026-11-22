"""Model reference parsing and provider resolution, offline (objects are inspected, never called)."""

from __future__ import annotations

import httpx
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from kestrel.config import Settings
from kestrel.errors import ConfigurationError, ProviderUnavailableError
from kestrel.llm import ModelRef, check_model, explain_provider_error, parse_model_ref, resolve_model
from kestrel.llm.catalog import ModelCatalog, load_model_catalog

FAKE = "fake-key-for-tests"


def with_keys(settings: Settings, **keys: str) -> Settings:
    return settings.model_copy(update={name: SecretStr(value) for name, value in keys.items()})


@pytest.mark.parametrize(
    ("text", "provider", "model"),
    [
        ("ollama:gpt-oss:20b", "ollama", "gpt-oss:20b"),
        ("ollama-cloud:gpt-oss:120b", "ollama-cloud", "gpt-oss:120b"),
        ("anthropic:claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5"),
        ("openai:gpt-6.1-sol", "openai", "gpt-6.1-sol"),
        ("  OLLAMA:qwen3.5:9b ", "ollama", "qwen3.5:9b"),
        ("ollama:library/model:tag", "ollama", "library/model:tag"),
    ],
)
def test_parse_reference_splits_on_first_colon(text: str, provider: str, model: str) -> None:
    ref = parse_model_ref(text)
    assert (ref.provider, ref.model) == (provider, model)
    assert str(ref) == f"{provider}:{model}"


@pytest.mark.parametrize("text", ["", "   ", "gpt-oss", "ollama:", ":gpt-oss", "ollama: bad name", "anthropic:-x"])
def test_malformed_reference_names_the_fix(text: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        parse_model_ref(text)
    assert "KESTREL_MODEL" in info.value.user_message or "provider's exact model id" in info.value.user_message


def test_unknown_provider_lists_the_valid_ones() -> None:
    with pytest.raises(ConfigurationError) as info:
        resolve_model("gemini:pro", Settings(_env_file=None))
    message = info.value.user_message
    assert "gemini" in message and "ollama-cloud" in message and "anthropic" in message


def test_parse_accepts_an_existing_ref() -> None:
    ref = ModelRef("anthropic", "claude-haiku-5-5")
    assert parse_model_ref(ref) is ref


def test_local_ollama_sends_explicit_context_and_never_the_cloud_key(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", FAKE)  # the ollama client would otherwise attach it
    resolved = resolve_model("ollama:gpt-oss:20b", settings)
    chat = resolved.chat_model
    assert isinstance(chat, ChatOllama)
    assert chat.base_url == settings.ollama_base_url
    assert chat.temperature == 0
    assert chat.num_ctx == 32768 and chat.num_predict == 8192
    assert "authorization" not in chat._client._client.headers
    assert "authorization" not in chat._async_client._client.headers
    assert chat._client._client.timeout.read == 120
    assert resolved.locality == "on-device" and resolved.tested
    assert resolved.label == "ollama:gpt-oss:20b"


def test_ollama_on_another_host_is_external(settings: Settings) -> None:
    remote = settings.model_copy(update={"ollama_base_url": "http://gpu-box.internal:11434"})
    resolved = resolve_model("ollama:gpt-oss:20b", remote)
    assert resolved.locality == "external"
    assert resolved.chat_model.base_url == "http://gpu-box.internal:11434"


def test_ollama_url_with_credentials_is_rejected(settings: Settings) -> None:
    bad = settings.model_copy(update={"ollama_base_url": "http://user:pw@127.0.0.1:11434"})
    with pytest.raises(ConfigurationError, match="user name or password"):
        resolve_model("ollama:gpt-oss:20b", bad)


def test_ollama_cloud_uses_ollama_com_with_a_bearer_header(settings: Settings) -> None:
    resolved = resolve_model("ollama-cloud:gpt-oss:120b", with_keys(settings, ollama_api_key=FAKE))
    chat = resolved.chat_model
    assert isinstance(chat, ChatOllama)
    assert chat.base_url == "https://ollama.com"
    assert chat._client._client.headers["authorization"] == f"Bearer {FAKE}"
    assert str(chat._client._client.base_url).startswith("https://ollama.com")
    assert resolved.locality == "external" and resolved.tested


def test_anthropic_resolves_with_pinned_url_and_no_temperature(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.invalid")
    resolved = resolve_model("anthropic:claude-sonnet-5-5", with_keys(settings, anthropic_api_key=FAKE))
    chat = resolved.chat_model
    assert isinstance(chat, ChatAnthropic)
    assert chat.anthropic_api_url == "https://api.anthropic.com"
    assert chat.anthropic_api_key.get_secret_value() == FAKE
    assert chat.temperature is None  # current Claude models reject non-default temperature
    assert chat.max_tokens == 8192 and chat.max_retries == 2 and chat.default_request_timeout == 120
    assert resolved.locality == "external" and resolved.tested


def test_openai_resolves_with_pinned_url(settings: Settings) -> None:
    keyed = with_keys(settings, openai_api_key=FAKE)
    reasoning = resolve_model("openai:gpt-6.1-sol", keyed).chat_model
    assert isinstance(reasoning, ChatOpenAI)
    assert reasoning.openai_api_base == "https://api.openai.com/v1"
    assert reasoning.temperature is None
    assert reasoning.max_tokens == 8192 and reasoning.max_retries == 2 and reasoning.store is False
    classic = resolve_model("openai:gpt-4.1-mini", keyed)
    assert classic.chat_model.temperature == 0
    assert not classic.tested


@pytest.mark.parametrize(
    ("ref", "env_name"),
    [("anthropic:claude-sonnet-5-5", "ANTHROPIC_API_KEY"), ("openai:gpt-6.1-sol", "OPENAI_API_KEY"), ("ollama-cloud:gpt-oss:120b", "OLLAMA_API_KEY")],
)
def test_missing_key_names_the_setting(settings: Settings, ref: str, env_name: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        resolve_model(ref, settings)
    assert f"set {env_name} in .env" in info.value.user_message


def test_catalog_defaults_when_file_missing(tmp_path) -> None:
    catalog = load_model_catalog(tmp_path)
    assert catalog == ModelCatalog()
    assert not catalog.is_tested("ollama", "gpt-oss:20b")


def test_invalid_catalog_names_the_field(tmp_path) -> None:
    (tmp_path / "models.yaml").write_text("ollama:\n  num_ctx: 10\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="ollama.num_ctx"):
        load_model_catalog(tmp_path)


def _tags(*names: str) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json={"models": [{"name": n, "model": n} for n in names]}))


def test_check_model_ollama_ready_missing_and_unreachable(settings: Settings) -> None:
    ok = check_model("ollama:gpt-oss:20b", settings, transport=_tags("gpt-oss:20b"))
    assert ok.status == "ok"
    missing = check_model("ollama:gpt-oss:20b", settings, transport=_tags("qwen3.5:9b"))
    assert missing.status == "fail" and missing.fix == "ollama pull gpt-oss:20b"

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    down = check_model("ollama:gpt-oss:20b", settings, transport=httpx.MockTransport(refuse))
    assert down.status == "fail" and "start Ollama and run: ollama pull gpt-oss:20b" == down.fix


def test_check_model_untested_model_warns(settings: Settings) -> None:
    result = check_model("ollama:qwen3.5:9b", settings, transport=_tags("qwen3.5:9b"))
    assert result.status == "warn" and "not on the tested list" in result.detail


def test_check_model_hosted_reports_key_presence_without_value(settings: Settings) -> None:
    assert check_model("anthropic:claude-sonnet-5-5", settings).fix == "set ANTHROPIC_API_KEY in .env"
    present = check_model("anthropic:claude-sonnet-5-5", with_keys(settings, anthropic_api_key=FAKE))
    assert present.status == "ok" and FAKE not in present.detail
    assert check_model("nonsense", settings).status == "fail"


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider said no")
        self.status_code = status_code


def test_explain_provider_error_gives_the_fix() -> None:
    down = explain_provider_error(ConnectionError("Failed to connect"), "ollama:gpt-oss:20b")
    assert isinstance(down, ProviderUnavailableError) and "ollama pull gpt-oss:20b" in down.user_message
    missing = explain_provider_error(_StatusError(404), "ollama:gpt-oss:20b")
    assert "ollama pull gpt-oss:20b" in missing.user_message
    auth = explain_provider_error(_StatusError(401), "anthropic:claude-sonnet-5-5")
    assert "ANTHROPIC_API_KEY" in auth.user_message
    limited = explain_provider_error(_StatusError(429), "openai:gpt-6.1-sol")
    assert "rate limiting" in limited.user_message
