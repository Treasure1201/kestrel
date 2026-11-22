"""Evaluation records (one JSONL line per question per run) and the workbook layout."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from kestrel.contracts._base import Frozen
from kestrel.contracts.answers import AskResult


class Assessment(StrEnum):
    """The values allowed by the workbook's assessment dropdown, spelled exactly."""

    CORRECT = "Correct"
    PARTIALLY_CORRECT = "Partially correct"
    INCORRECT = "Incorrect"
    CORRECTLY_DECLINED = "Correctly declined"
    INCORRECTLY_DECLINED = "Incorrectly declined"


GradeCategory = Literal[
    "correct",
    "partially_correct",
    "incorrect",
    "correctly_declined",
    "incorrectly_declined",
    "withheld",
    "retrieval_miss",
    "error",
]
"""Outcome counts reported separately by evaluation (finer than `Assessment`)."""


class AutoGrade(Frozen):
    """Automatic grade against the gold file. Kept separate from the human assessment."""

    category: GradeCategory
    assessment: Assessment | None = None
    """The workbook value this grade maps to; None for withheld, retrieval miss and error."""
    missing_facts: tuple[str, ...] = ()
    dropped_conditions: tuple[str, ...] = ()
    forbidden_found: tuple[str, ...] = ()
    expected_section_retrieved: bool | None = None
    notes: str = ""


class EvalRecord(Frozen):
    """One question asked once with one model. Written as one JSONL line; re-gradable offline."""

    schema_version: int = 1
    question_id: str = Field(pattern=r"^Q[1-9][0-9]*$")
    question: str
    """The question exactly as in the workbook (before screening)."""
    bot_id: str
    model: str
    run: int = Field(ge=1)
    result: AskResult
    auto_grade: AutoGrade | None = None
    started_at: datetime
    finished_at: datetime


class WorkbookLayout:
    """Where things are in the supplied test-questions workbook (verified with openpyxl).

    Row 5 is the header, row 6 is a grey worked example (`Ex`) that must not be
    overwritten, and Q1..Q15 occupy rows 7..21. The assessment column carries a list
    validation over E7:E21 with the `Assessment` values, and a `Summary` sheet counts
    them with formulas; writers must keep both intact.
    """

    SHEET = "Test Questions"
    SUMMARY_SHEET = "Summary"
    HEADER_ROW = 5
    EXAMPLE_ROW = 6
    FIRST_QUESTION_ROW = 7
    LAST_QUESTION_ROW = 21
    QUESTION_COUNT = 15
    ID_COLUMN = "A"
    QUESTION_COLUMN = "B"
    ANSWER_COLUMN = "C"
    SOURCES_COLUMN = "D"
    ASSESSMENT_COLUMN = "E"
    COMMENTS_COLUMN = "F"
    ASSESSMENT_VALIDATION_RANGE = "E7:E21"

    @classmethod
    def row_for(cls, question_id: str) -> int:
        """Return the worksheet row of `Q<n>`; raises ValueError outside Q1..Q15."""
        number = int(question_id.removeprefix("Q"))
        if not 1 <= number <= cls.QUESTION_COUNT:
            raise ValueError(f"no workbook row for {question_id}")
        return cls.FIRST_QUESTION_ROW + number - 1
