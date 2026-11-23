"""The 'Evaluation runs' tab: run folders compared per model, and a per-question grid."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from kestrel.contracts import EvalRecord

from kestrel_app import metrics, results
from kestrel_app.components import GRID_COLOURS, fmt_latency, legend, md, outcome_badge, quote, section_label
from kestrel_app.results import RunFolder


def _cost(value: float | None, unpriced: int) -> str:
    if value is None:
        return "unknown"
    text = f"US${value:.4f}"
    return f"{text} ({unpriced} unpriced)" if unpriced else text


def _hit_rate(summary: metrics.RunSummary) -> str:
    if summary.hit_rate is None:
        return "n/a"
    return f"{summary.retrieval_hits} of {summary.retrieval_targets} ({summary.hit_rate:.0%})"


def _summary_rows(folders: list[RunFolder]) -> list[dict[str, object]]:
    rows = []
    for folder in folders:
        s = metrics.summarise_run(folder.name, folder.records)
        row: dict[str, object] = {"Run folder": folder.name, "Model": s.model, "Runs": s.runs, "Answers": s.answers}
        for grade in metrics.GRADE_ORDER:
            row[metrics.GRADE_STYLE[grade][1].capitalize()] = s.grades.get(grade, 0)
        row["Not graded"] = s.grades.get("ungraded", 0)
        row["Retrieval hit rate"] = _hit_rate(s)
        row["Cost per question"] = _cost(s.cost_per_question, s.unpriced)
        row["Median latency"] = fmt_latency(s.median_latency_ms)
        row["Human review"] = f"{len(folder.reviews)} of {s.answers} assessed" if folder.reviews else "not reviewed"
        rows.append(row)
    return rows


def _cell_style(value: object) -> str:
    bg, fg = GRID_COLOURS.get(str(value), ("transparent", "inherit")) if value else ("transparent", "inherit")
    return f"background-color:{bg};color:{fg};text-align:center;font-weight:600"


def _detail(record: EvalRecord, review: results.HumanReview | None) -> None:
    result = record.result
    with st.container(border=True):
        st.markdown(f"**{record.question_id}** · `{record.model}` · run {record.run}")
        st.markdown(md(record.question))
        outcome_badge(result.outcome)
        section_label("Answer")
        st.markdown(md(result.answer) if result.answer else "_No answer text._")
        if result.reason:
            st.markdown(f"**Why:** {md(result.reason)}")
        section_label("Sources")
        if not result.citations:
            st.caption("No citations.")
        for citation in result.citations:
            where = f"tool result: {citation.title} {citation.locator}" if citation.is_tool else f"{citation.filename}, {citation.locator}"
            st.caption(f"{citation.label} · {where}")
            quote(citation.quote)
        left, right = st.columns(2)
        with left:
            section_label("Automatic grade")
            grade = record.auto_grade
            if grade is None:
                st.caption("Not graded.")
            else:
                st.markdown(f"**{metrics.GRADE_STYLE.get(grade.category, metrics.UNGRADED)[1]}**")
                reasons = [
                    *(f"missing fact: {f}" for f in grade.missing_facts),
                    *(f"dropped condition: {c}" for c in grade.dropped_conditions),
                    *(f"forbidden text found: {t}" for t in grade.forbidden_found),
                ]
                if grade.expected_section_retrieved is not None:
                    reasons.append("expected section retrieved" if grade.expected_section_retrieved else "expected section never retrieved")
                if grade.notes:
                    reasons.append(grade.notes)
                st.markdown("\n".join(f"- {md(r)}" for r in reasons) or "_No reasons recorded._")
                st.caption("An automatic string-matching check against the gold file, not an assessment.")
        with right:
            section_label("Human assessment")
            if review is None:
                st.caption("Not reviewed yet.")
            else:
                st.markdown(f"**{md(review.assessment)}**")
                if review.comment:
                    st.caption(review.comment)


def _retrieval(selected: list[RunFolder], root: Path) -> None:
    """Three bars, hit@1, hit@3 and hit@6: from the newest selected folder with a search-only pass, else the newest anywhere."""
    folder = next((f for f in selected if f.retrieval is not None and f.retrieval.targets), None)
    latest = (folder.name, folder.retrieval) if folder is not None and folder.retrieval is not None else results.latest_retrieval(root)
    section_label("Retrieval hit rate, search only")
    if latest is None:
        st.caption("No search-only pass has been run yet. Run `uv run python -m eval.retrieval_eval` (or `eval.run_eval`).")
        return
    name, found = latest
    for cutoff in (1, 3, 6):
        share = found.share(cutoff) or 0.0
        st.progress(share, text=f"Hit@{cutoff}: {found.hits.get(cutoff, 0)} of {found.targets} ({share:.0%})")
    mrr = f" MRR {found.mrr:.3f}." if found.mrr is not None else ""
    st.caption(
        f"From {name}: where the expected section ranked among the top {found.search_limit} search results, "
        f"with no model.{mrr} {found.targets} questions have a target section; the {found.without_target} "
        "unanswerable questions have no target section and are not counted."
    )


def render(root: Path) -> None:
    """Draw the tab from the run folders under `root`."""
    folders = results.discover_runs(root)
    if not folders:
        st.info(
            f"No evaluation runs found under `{root.as_posix()}/`. Run `uv run python -m eval.run_eval` to create one.",
            icon=":material/science:",
        )
        if results.latest_retrieval(root) is not None:
            _retrieval([], root)
        return
    for folder in folders:
        if folder.problem:
            st.warning(f"{folder.name}: {folder.problem}")
    names = [f.name for f in folders]
    default = [f.name for f in results.latest_per_model(folders)]
    chosen = st.multiselect("Run folders (latest per model by default)", names, default=default, key="eval_folders")
    selected = [f for f in folders if f.name in chosen and f.records]
    if not selected:
        st.caption("Choose at least one run folder with records.")
        return

    st.dataframe(_summary_rows(selected), hide_index=True)
    st.caption("Grades are automatic checks against the gold file, not human assessments. Retrieval hit rate counts questions whose expected section was retrieved.")
    _retrieval(selected, root)

    runs = sorted({run for f in selected for run in f.runs})
    run = st.selectbox("Run number", runs, key="eval_run") if len(runs) > 1 else runs[0]
    ids = metrics.all_question_ids(f.records for f in selected)
    labels = [f"{f.model} ({f.name})" for f in selected]
    table = pd.DataFrame(
        [[metrics.grade_letter(metrics.grid(f.records, run).get(qid)) for qid in ids] for f in selected],
        index=labels,
        columns=ids,
    )
    section_label(f"Per question, run {run}")
    event = st.dataframe(table.style.map(_cell_style), on_select="rerun", selection_mode="single-cell", key="eval_grid")
    legend([(letter, meaning) for letter, meaning in (*metrics.GRADE_STYLE.values(), metrics.UNGRADED)])
    cells = event.selection.cells if event is not None else []
    if not cells:
        st.caption("Select a cell to see the answer, its sources, the automatic grade and the human assessment side by side.")
        return
    row, column = cells[0]
    if not (0 <= row < len(selected)) or column not in ids:
        return
    folder = selected[row]
    record = metrics.grid(folder.records, run).get(column)
    if record is None:
        st.caption(f"{column} was not asked in run {run} of {folder.name}.")
        return
    _detail(record, folder.reviews.get((run, column)))
