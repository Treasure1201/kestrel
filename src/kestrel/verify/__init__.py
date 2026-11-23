"""Deterministic evidence checks over the model's answer. Plain code; never trusts the model."""

from kestrel.verify.checks import (
    WITHDRAWN_REASON,
    check_sources_still_current,
    clean_label,
    evidence_report,
    repair_message,
    run_checks,
)
from kestrel.verify.numbers import comparable, normalise_text, numbers_in
from kestrel.verify.quotes import quote_found

__all__ = [
    "WITHDRAWN_REASON",
    "check_sources_still_current",
    "clean_label",
    "comparable",
    "evidence_report",
    "normalise_text",
    "numbers_in",
    "quote_found",
    "repair_message",
    "run_checks",
]
