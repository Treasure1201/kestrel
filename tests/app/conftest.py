"""Put the app folder and this folder on sys.path, and reset the app's service seam after each test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parents[1] / "app"
for folder in (APP_DIR, HERE):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


@pytest.fixture(autouse=True)
def _reset_service_seam():
    yield
    from kestrel_app import runtime

    runtime.use_service(None)
