"""Fixtures for store tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docbuilders import make_store  # noqa: E402
from fakes import FakeEmbedder  # noqa: E402

from kestrel.store import Store  # noqa: E402


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def store(tmp_path: Path, embedder: FakeEmbedder) -> Store:
    return make_store(tmp_path / "kestrel.sqlite", embedder)
