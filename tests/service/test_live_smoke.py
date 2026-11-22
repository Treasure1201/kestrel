"""Live smoke test against a real provider and the real documents. Run with: uv run pytest -m live"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kestrel.config import Settings
from kestrel.contracts import Outcome
from kestrel.service import build_service

pytestmark = pytest.mark.live
REPO = Path(__file__).resolve().parents[2]


def test_waiting_period_question_passes_the_evidence_checks(tmp_path: Path) -> None:
    settings = Settings(_env_file=REPO / ".env", db=tmp_path / "live.sqlite", bots_dir=REPO / "bots",
                        source_dir=REPO / "data" / "source", config_dir=REPO / "config")
    if not settings.key_present("anthropic_api_key") and not os.environ.get("KESTREL_MODEL"):
        pytest.skip("no provider key configured")
    service = build_service(settings)
    operator = settings.principal("cli")
    service.ingest(operator)
    result = service.ask("kestrel-products", "What waiting period applies to disability income Option B?", operator)
    assert result.outcome == Outcome.ANSWERED, (result.reason, result.evidence_report)
    assert result.evidence_report is not None and result.evidence_report.passed
    assert result.citations
