"""Usage accounting and cost."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kestrel.contracts import Usage
from kestrel.errors import ConfigurationError
from kestrel.llm import Price, PriceTable, cost_usd, load_prices, usage_from_messages


def _ai(input_tokens: int, output_tokens: int) -> AIMessage:
    return AIMessage(
        content="x",
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
    )


def test_usage_sums_ai_messages_only() -> None:
    messages = [HumanMessage("q"), _ai(1000, 200), ToolMessage("r", tool_call_id="t"), _ai(1500, 300), AIMessage("no usage")]
    usage = usage_from_messages(messages)
    assert usage == Usage(model_calls=3, input_tokens=2500, output_tokens=500)


def test_cost_from_repository_prices(repo_root: Path) -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000)
    prices = load_prices(repo_root / "config")
    assert prices is not None and prices.as_of == date(2026, 11, 3)
    assert cost_usd("anthropic", "claude-sonnet-5-5", usage, prices=prices) == pytest.approx(2.0 + 1.0)
    assert cost_usd("anthropic", "claude-haiku-5-5", usage, config_dir=repo_root / "config") == pytest.approx(0.1 + 0.05)


def test_unknown_price_and_local_models_are_none_not_zero(repo_root: Path) -> None:
    usage = Usage(input_tokens=10, output_tokens=10)
    prices = load_prices(repo_root / "config")
    assert cost_usd("openai", "gpt-6.1-sol", usage, prices=prices) is None
    assert cost_usd("ollama-cloud", "gpt-oss:120b", usage, prices=prices) is None
    free_local = PriceTable(as_of=date(2026, 1, 1), prices={"ollama": {"gpt-oss:20b": Price(input=0, output=0)}})
    assert cost_usd("ollama", "gpt-oss:20b", usage, prices=free_local) is None
    assert cost_usd("anthropic", "claude-sonnet-5-5", usage, config_dir=Path("/nonexistent-folder")) is None


def test_invalid_pricing_file_names_the_field(tmp_path: Path) -> None:
    (tmp_path / "pricing.yaml").write_text("as_of: 2026-01-01\nprices:\n  anthropic:\n    m: {input: -1, output: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="prices.anthropic.m.input"):
        load_prices(tmp_path)
