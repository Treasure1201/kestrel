"""Shared fixtures. Tests are offline: no model provider, no network, no real `.env`."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from kestrel.config import Settings
from kestrel.store.schema import open_database

# Keep framework tracing off for every test, whatever the developer's shell has set.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the repository."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def source_documents() -> Path:
    """The supplied source documents of the product bot's collection."""
    return REPO_ROOT / "data" / "source" / "kestrel-products"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings that point every path at a temporary folder and ignore the real environment."""
    for name in list(os.environ):
        if name.startswith("KESTREL_") or name in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY"}:
            monkeypatch.delenv(name, raising=False)
    return Settings(
        _env_file=None,
        db=tmp_path / "kestrel.sqlite",
        bots_dir=REPO_ROOT / "bots",
        source_dir=REPO_ROOT / "data" / "source",
        config_dir=REPO_ROOT / "config",
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database in a temporary folder."""
    conn = open_database(tmp_path / "test.sqlite")
    try:
        yield conn
    finally:
        conn.close()
