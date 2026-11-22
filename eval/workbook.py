"""Filled copies of the assessor's workbook, written without disturbing its structure.

The template is opened read-only in effect: copies are always saved to another path,
and every save is verified by reloading the copy and comparing it with the template
(the assessment dropdown on E7:E21, the Summary sheet's formulas, column widths, the
grey example row and the header). Column E, the human assessment, is written only from
a completed review; the automatic grade goes to column F, prefixed "Auto-grade:".
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.workbook.workbook import Workbook

from kestrel.contracts import AskResult, Assessment, EvalRecord, Outcome, WorkbookLayout

from eval.artefacts import source_lines
from eval.grading import describe

L = WorkbookLayout
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "KA_Test_Questions.xlsx"
MAX_CELL = 32_000


class WorkbookError(RuntimeError):
    """The template is missing or a written copy failed verification."""


def _cell_text(text: str) -> str:
    text = ILLEGAL_CHARACTERS_RE.sub("", text or "")
    if text.startswith(("=", "+", "-", "@")):
        text = " " + text  # never let answer text become a formula
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 20] + " [truncated]"


def answer_cell(result: AskResult) -> str:
    """Column C: what the user saw, with the decline reason and caveats."""
    if result.outcome == Outcome.ERROR:
        category = result.error_category.value if result.error_category else "unknown"
        return f"[Error: {category}] {result.reason or result.answer}".strip()
    parts: list[str] = []
    if result.outcome == Outcome.WITHHELD:
        parts.append("[Withheld: the evidence checks could not confirm this answer against the sources.]")
    elif result.outcome == Outcome.PARTIAL:
        parts.append("[Partial answer]")
    if result.answer:
        parts.append(result.answer)
    if result.outcome in {Outcome.NOT_FOUND, Outcome.REFUSED}:
        reason = result.reason or result.clarification
        label = "Declined" if result.outcome == Outcome.NOT_FOUND else "Refused"
        parts.append(f"{label}: {reason}" if reason else f"{label} (no reason given).")
    if result.caveats:
        parts.append("Caveats: " + " ".join(result.caveats))
    return "\n\n".join(parts)


def question_texts(template: Path = DEFAULT_TEMPLATE) -> dict[str, str]:
    """Q1..Q15 and their question text, read from the workbook."""
    wb = _load(template)
    ws = wb[L.SHEET]
    out: dict[str, str] = {}
    for row in range(L.FIRST_QUESTION_ROW, L.LAST_QUESTION_ROW + 1):
        qid = str(ws[f"{L.ID_COLUMN}{row}"].value or "").strip()
        text = str(ws[f"{L.QUESTION_COLUMN}{row}"].value or "").strip()
        if qid and text:
            out[qid] = text
    return out


def _load(path: Path) -> Workbook:
    if not Path(path).is_file():
        raise WorkbookError(f"workbook not found: {path}")
    return openpyxl.load_workbook(path)


def _guard(template: Path, out_path: Path) -> None:
    if Path(out_path).resolve() == Path(template).resolve():
        raise WorkbookError("refusing to write to the template workbook; choose another output path")


def fill_workbook(
    template: Path,
    out_path: Path,
    records: Mapping[str, EvalRecord],
    assessments: Mapping[str, tuple[Assessment, str]] | None = None,
) -> Path:
    """Write answers, sources and comments for Q1..Q15 into a copy of `template`.

    `records` maps question id to its record. `assessments` (from a completed review)
    maps question id to the human assessment and comment; without it column E stays
    empty and column F holds the automatic grade.
    """
    _guard(template, out_path)
    wb = _load(template)
    ws = wb[L.SHEET]
    assessments = assessments or {}
    for qid, record in records.items():
        try:
            row = L.row_for(qid)
        except ValueError:
            continue
        ws[f"{L.ANSWER_COLUMN}{row}"] = _cell_text(answer_cell(record.result))
        ws[f"{L.SOURCES_COLUMN}{row}"] = _cell_text("\n".join(source_lines(record.result)))
        auto = describe(record.auto_grade) if record.auto_grade else "Auto-grade: not graded."
        if qid in assessments:
            value, comment = assessments[qid]
            ws[f"{L.ASSESSMENT_COLUMN}{row}"] = Assessment(value).value
            ws[f"{L.COMMENTS_COLUMN}{row}"] = _cell_text(comment.strip() or auto)
        else:
            ws[f"{L.COMMENTS_COLUMN}{row}"] = _cell_text(auto)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    problems = verify_workbook(template, out_path, {q: a.value for q, (a, _) in assessments.items()})
    if problems:
        raise WorkbookError(f"{out_path} failed verification: " + "; ".join(problems))
    return Path(out_path)


def _validations(wb: Workbook) -> list[tuple[str, str, str | None]]:
    ws = wb[L.SHEET]
    return sorted((str(dv.sqref), str(dv.type), dv.formula1) for dv in ws.data_validations.dataValidation)


def _formulas(wb: Workbook) -> dict[str, object]:
    ws = wb[L.SUMMARY_SHEET]
    return {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}


def _widths(wb: Workbook) -> dict[str, dict[str, float | None]]:
    return {ws.title: {k: v.width for k, v in ws.column_dimensions.items()} for ws in wb}


def verify_workbook(template: Path, copy: Path, expected_assessments: Mapping[str, str] | None = None) -> list[str]:
    """Compare a written copy with the template; return a list of problems (empty when intact)."""
    original, written = _load(template), _load(copy)
    problems: list[str] = []
    if written.sheetnames != original.sheetnames:
        problems.append(f"sheets changed: {written.sheetnames}")
        return problems
    validations = _validations(written)
    if validations != _validations(original):
        problems.append(f"data validation changed: {validations}")
    if not any(L.ASSESSMENT_VALIDATION_RANGE in sqref for sqref, _, _ in validations):
        problems.append(f"no validation on {L.ASSESSMENT_VALIDATION_RANGE}")
    if _formulas(written) != _formulas(original):
        problems.append("Summary sheet formulas changed")
    if _widths(written) != _widths(original):
        problems.append("column widths changed")
    ws_o, ws_w = original[L.SHEET], written[L.SHEET]
    for row in range(1, L.EXAMPLE_ROW + 1):
        for col in "ABCDEF":
            if ws_o[f"{col}{row}"].value != ws_w[f"{col}{row}"].value:
                problems.append(f"{col}{row} changed")
    for row in range(L.HEADER_ROW, L.LAST_QUESTION_ROW + 1):
        for col in "ABCDEF":
            a, b = ws_o[f"{col}{row}"], ws_w[f"{col}{row}"]
            if (a.fill.fgColor.rgb, a.font.b, a.alignment.wrap_text) != (b.fill.fgColor.rgb, b.font.b, b.alignment.wrap_text):
                problems.append(f"{col}{row} style changed")
    for row in range(L.FIRST_QUESTION_ROW, L.LAST_QUESTION_ROW + 1):
        for col in (L.ID_COLUMN, L.QUESTION_COLUMN):
            if ws_o[f"{col}{row}"].value != ws_w[f"{col}{row}"].value:
                problems.append(f"{col}{row} changed")
        qid = str(ws_w[f"{L.ID_COLUMN}{row}"].value)
        value = ws_w[f"{L.ASSESSMENT_COLUMN}{row}"].value
        expected = (expected_assessments or {}).get(qid)
        if value != expected and not (value in (None, "") and expected is None):
            problems.append(f"{L.ASSESSMENT_COLUMN}{row} is {value!r}, expected {expected!r}")
    return problems
