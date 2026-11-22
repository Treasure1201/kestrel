"""Fixtures for ingest tests: in-memory repository and vector index, and the product bot's knowledge config."""

from __future__ import annotations

import pytest
from ingest.ingest_fakes import COLLECTION, MANIFEST, MemoryRepository, MemoryVectorIndex

from kestrel.contracts import KnowledgeConfig


@pytest.fixture
def repository() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def vectors() -> MemoryVectorIndex:
    return MemoryVectorIndex()


@pytest.fixture
def knowledge() -> KnowledgeConfig:
    return KnowledgeConfig(collections=(COLLECTION,), approval="required", manifest=MANIFEST)
