"""Fixtures for the evaluation harness tests: repository root on the path, a fake service."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for entry in (str(_HERE), str(_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from eval_helpers import FakeService, good_answers  # noqa: E402


@pytest.fixture
def fake_service() -> FakeService:
    """A service that returns a correct, cited answer for each of the 15 questions."""
    return FakeService(good_answers())


@pytest.fixture
def template() -> Path:
    return _ROOT / "eval" / "KA_Test_Questions.xlsx"
