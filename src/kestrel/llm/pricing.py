"""Token usage read from model responses, and cost from `config/pricing.yaml`.

Cost is `None`, never 0, when it is not known: for local models (no API charge)
and for any model without a price entry. Prices are list prices per million tokens;
cache discounts are not modelled, so cost is an upper estimate when caching applies.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import Field, ValidationError

from kestrel.contracts import Frozen, ProviderName, Usage
from kestrel.errors import ConfigurationError
from kestrel.llm.catalog import describe_validation_error, read_yaml_mapping

PRICING_FILE = "pricing.yaml"
_LOCAL_PROVIDERS = frozenset({"ollama"})


class Price(Frozen):
    """List price in USD per million tokens."""

    input: float = Field(ge=0, le=1000)
    output: float = Field(ge=0, le=1000)


class PriceTable(Frozen):
    """Contents of `config/pricing.yaml`."""

    as_of: date
    currency: str = Field(default="USD", pattern=r"^USD$")
    prices: dict[ProviderName, dict[str, Price]] = Field(default_factory=dict)

    def price(self, provider: str, model: str) -> Price | None:
        """The price entry for a model, or None when unknown."""
        return self.prices.get(provider, {}).get(model)  # type: ignore[call-overload]


@lru_cache(maxsize=8)
def _load(path: Path, mtime_ns: int) -> PriceTable:
    data = read_yaml_mapping(path, "pricing file")
    try:
        return PriceTable.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"{path}: {describe_validation_error(exc)}") from None


def load_prices(config_dir: Path) -> PriceTable | None:
    """Load `config_dir/pricing.yaml`; None when the file does not exist (every cost is then unknown)."""
    path = (config_dir / PRICING_FILE).resolve()
    if not path.is_file():
        return None
    return _load(path, path.stat().st_mtime_ns)


def usage_from_messages(messages: Iterable[BaseMessage]) -> Usage:
    """Sum LangChain `usage_metadata` over the AI messages; each AI message counts as one model call.

    Searches, repairs and cost are left at their defaults for the caller to fill.
    """
    calls = input_tokens = output_tokens = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        calls += 1
        meta = message.usage_metadata or {}
        input_tokens += int(meta.get("input_tokens") or 0)
        output_tokens += int(meta.get("output_tokens") or 0)
    return Usage(model_calls=calls, input_tokens=input_tokens, output_tokens=output_tokens)


def cost_usd(provider: str, model: str, usage: Usage, *, prices: PriceTable | None = None, config_dir: Path | None = None) -> float | None:
    """Cost of `usage` in USD, or None for local models and models without a known price.

    `prices` defaults to `config_dir/pricing.yaml` (`config/` when not given).
    """
    if provider in _LOCAL_PROVIDERS:
        return None
    table = prices if prices is not None else load_prices(config_dir or Path("config"))
    if table is None:
        return None
    price = table.price(provider, model)
    if price is None:
        return None
    total = (usage.input_tokens * price.input + usage.output_tokens * price.output) / 1_000_000
    return round(total, 6)
