"""Resolve `<provider>:<model>` to a LangChain chat model, and check it cheaply for `doctor`.

Construction never touches the network. Base URLs and keys are always passed
explicitly, so provider environment variables (`ANTHROPIC_BASE_URL`,
`OPENAI_BASE_URL`, gateway switches) cannot silently redirect traffic past the
egress policy.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from kestrel.config import Settings
from kestrel.contracts import DoctorCheck, Locality
from kestrel.errors import ConfigurationError, KestrelError, ProviderUnavailableError
from kestrel.llm.catalog import ModelCatalog, load_model_catalog
from kestrel.llm.policy import is_loopback_url, model_locality, url_host
from kestrel.llm.refs import ModelRef, parse_model_ref
from kestrel.redaction import describe_exception, http_status

OLLAMA_CLOUD_URL = "https://ollama.com"
ANTHROPIC_URL = "https://api.anthropic.com"
OPENAI_URL = "https://api.openai.com/v1"

_KEYS = {
    "ollama-cloud": ("ollama_api_key", "OLLAMA_API_KEY"),
    "anthropic": ("anthropic_api_key", "ANTHROPIC_API_KEY"),
    "openai": ("openai_api_key", "OPENAI_API_KEY"),
}


@dataclass(frozen=True)
class ResolvedModel:
    """A ready-to-use chat model and what it is."""

    chat_model: BaseChatModel
    ref: ModelRef
    locality: Locality
    tested: bool
    """Whether the model is on the tested list in `config/models.yaml`."""

    @property
    def provider(self) -> str:
        """Provider prefix, e.g. `anthropic`."""
        return self.ref.provider

    @property
    def model(self) -> str:
        """Model name without the provider prefix."""
        return self.ref.model

    @property
    def label(self) -> str:
        """`<provider>:<model>`, as recorded in the audit log."""
        return str(self.ref)


def _key(ref: ModelRef, settings: Settings) -> SecretStr:
    field, env_name = _KEYS[ref.provider]
    value = getattr(settings, field, None)
    if not isinstance(value, SecretStr) or not value.get_secret_value().strip():
        raise ConfigurationError(
            f"{ref} needs an API key: set {env_name} in .env (copy .env.example to .env if you have not yet)."
        )
    return SecretStr(value.get_secret_value().strip())


def _ollama_base_url(settings: Settings) -> str:
    url = settings.ollama_base_url.strip().rstrip("/")
    url_host(url)
    if "@" in url.split("//", 1)[-1].split("/", 1)[0]:
        raise ConfigurationError("KESTREL_OLLAMA_BASE_URL must not contain a user name or password.")
    return url


def _strip_ambient_auth(chat: BaseChatModel) -> None:
    """The ollama client adds `OLLAMA_API_KEY` from the environment to every client.

    A local or self-hosted Ollama must never receive the cloud key, so the header is
    removed from both underlying httpx clients after construction.
    """
    for attr in ("_client", "_async_client"):
        wrapper = getattr(chat, attr, None)
        inner = getattr(wrapper, "_client", None)
        headers = getattr(inner, "headers", None)
        if headers is not None:
            headers.pop("authorization", None)


def _build_ollama(ref: ModelRef, settings: Settings, catalog: ModelCatalog) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    timeout = catalog.defaults.request_timeout_s
    common = {
        "model": ref.model,
        "temperature": 0,
        "num_ctx": catalog.ollama.num_ctx,
        "num_predict": catalog.ollama.num_predict,
    }
    if ref.provider == "ollama-cloud":
        key = _key(ref, settings).get_secret_value()
        return ChatOllama(
            **common,
            base_url=OLLAMA_CLOUD_URL,
            client_kwargs={
                "timeout": timeout,
                "follow_redirects": False,
                "headers": {"Authorization": f"Bearer {key}"},
            },
        )
    base_url = _ollama_base_url(settings)
    client_kwargs: dict[str, object] = {"timeout": timeout, "follow_redirects": False}
    if is_loopback_url(base_url):
        # An on-device endpoint must never be reached through an environment proxy
        # (HTTP_PROXY with NO_PROXY cleared would route loopback traffic off the machine).
        client_kwargs["trust_env"] = False
    chat = ChatOllama(
        **common,
        base_url=base_url,
        keep_alive=catalog.ollama.keep_alive,
        client_kwargs=client_kwargs,
    )
    _strip_ambient_auth(chat)
    return chat


def _build_anthropic(ref: ModelRef, settings: Settings, catalog: ModelCatalog) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    options: dict[str, object] = {}
    if catalog.accepts_temperature(ref.provider, ref.model):
        options["temperature"] = 0
    return ChatAnthropic(
        model=ref.model,
        api_key=_key(ref, settings),
        base_url=ANTHROPIC_URL,
        max_tokens=catalog.defaults.max_output_tokens,
        timeout=catalog.defaults.request_timeout_s,
        max_retries=catalog.defaults.max_retries,
        **options,
    )


def _build_openai(ref: ModelRef, settings: Settings, catalog: ModelCatalog) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    options: dict[str, object] = {}
    if catalog.accepts_temperature(ref.provider, ref.model):
        options["temperature"] = 0
    return ChatOpenAI(
        model=ref.model,
        api_key=_key(ref, settings),
        base_url=OPENAI_URL,
        max_tokens=catalog.defaults.max_output_tokens,
        timeout=catalog.defaults.request_timeout_s,
        max_retries=catalog.defaults.max_retries,
        store=False,
        **options,
    )


_BUILDERS = {
    "ollama": _build_ollama,
    "ollama-cloud": _build_ollama,
    "anthropic": _build_anthropic,
    "openai": _build_openai,
}


def resolve_model(ref: str | ModelRef, settings: Settings, *, catalog: ModelCatalog | None = None) -> ResolvedModel:
    """Build the chat model for `ref` without any network call.

    Raises:
        ConfigurationError: malformed reference, unknown provider, missing key or a bad Ollama URL.
    """
    parsed = parse_model_ref(ref)
    catalog = catalog or load_model_catalog(settings.config_dir)
    try:
        chat = _BUILDERS[parsed.provider](parsed, settings, catalog)
    except KestrelError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Could not set up {parsed}: {type(exc).__name__}.", detail=describe_exception(exc)) from None
    return ResolvedModel(
        chat_model=chat,
        ref=parsed,
        locality=model_locality(parsed, settings),
        tested=catalog.is_tested(parsed.provider, parsed.model),
    )


def ollama_pull_fix(model: str) -> str:
    """The command that makes a local model available."""
    return f"start Ollama and run: ollama pull {model}"


def explain_provider_error(exc: BaseException, ref: str | ModelRef) -> KestrelError:
    """Turn a provider or transport exception into a user-safe error with the fix.

    The exception text is never kept (it can echo headers or keys): `detail` holds
    only the exception class and the HTTP status, if any.
    """
    if isinstance(exc, KestrelError):
        return exc
    parsed = parse_model_ref(ref)
    status = http_status(exc)
    detail = describe_exception(exc)
    local = parsed.provider == "ollama"
    if local and (status == 404 or "not found" in str(exc).lower()):
        return ProviderUnavailableError(f"Model {parsed.model!r} is not available in Ollama: {ollama_pull_fix(parsed.model)}.", detail=detail)
    if local and isinstance(exc, (ConnectionError, httpx.TransportError)):
        return ProviderUnavailableError(f"Cannot reach Ollama at the configured KESTREL_OLLAMA_BASE_URL: {ollama_pull_fix(parsed.model)}.", detail=detail)
    if status in (401, 403) and parsed.provider in _KEYS:
        env_name = _KEYS[parsed.provider][1]
        return ProviderUnavailableError(f"{parsed.provider} rejected the API key: check {env_name} in .env.", detail=detail)
    if status == 404:
        return ProviderUnavailableError(f"{parsed.provider} does not offer model {parsed.model!r}: check KESTREL_MODEL.", detail=detail)
    if status == 429:
        return ProviderUnavailableError(f"{parsed.provider} is rate limiting requests: wait a minute and retry.", detail=detail)
    return ProviderUnavailableError(f"{parsed} could not be reached or failed: retry, or choose another model with --model.", detail=detail)


def _ollama_has_model(names: list[str], model: str) -> bool:
    wanted = model if ":" in model else f"{model}:latest"
    return wanted in names or model in names


def check_model(
    ref: str | ModelRef,
    settings: Settings,
    *,
    timeout_s: float = 5.0,
    transport: httpx.BaseTransport | None = None,
) -> DoctorCheck:
    """Cheap readiness check for `doctor`: key presence, and for local Ollama reachability and the pulled model.

    Spends no tokens. Never raises for an environment problem; the result says what to fix.
    """
    try:
        parsed = parse_model_ref(ref)
        catalog = load_model_catalog(settings.config_dir)
    except KestrelError as exc:
        return DoctorCheck(name="model", status="fail", detail=exc.user_message, fix="set KESTREL_MODEL=<provider>:<model> in .env")
    tested = catalog.is_tested(parsed.provider, parsed.model)
    note = "on the tested list" if tested else "not on the tested list (it works if it supports tool calling)"
    if parsed.provider in _KEYS:
        env_name = _KEYS[parsed.provider][1]
        if not settings.key_present(_KEYS[parsed.provider][0]):
            return DoctorCheck(name="model", status="fail", detail=f"{parsed}: {env_name} is not set", fix=f"set {env_name} in .env")
        return DoctorCheck(name="model", status="ok" if tested else "warn", detail=f"{parsed}: {env_name} is set; {note}")
    try:
        base = _ollama_base_url(settings)
    except KestrelError as exc:
        return DoctorCheck(name="model", status="fail", detail=exc.user_message, fix="set KESTREL_OLLAMA_BASE_URL=http://127.0.0.1:11434")
    try:
        with httpx.Client(timeout=timeout_s, transport=transport, trust_env=False, follow_redirects=False) as client:
            response = client.get(f"{base}/api/tags")
            response.raise_for_status()
            names = [str(item.get("model") or item.get("name")) for item in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        return DoctorCheck(
            name="model",
            status="fail",
            detail=f"{parsed}: cannot reach Ollama at {base} ({type(exc).__name__})",
            fix=ollama_pull_fix(parsed.model),
        )
    if not _ollama_has_model(names, parsed.model):
        return DoctorCheck(name="model", status="fail", detail=f"{parsed}: Ollama is running but the model is not pulled", fix=f"ollama pull {parsed.model}")
    return DoctorCheck(name="model", status="ok" if tested else "warn", detail=f"{parsed}: Ollama is running and the model is pulled; {note}")
