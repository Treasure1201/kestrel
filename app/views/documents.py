"""Documents and findings: what the selected assistant can read, its status, and what the scanner found."""

from __future__ import annotations

from collections import Counter

import streamlit as st

from kestrel.contracts import OPERATOR_ROLE, AssistantService, AuditRecord, DocumentRecord, DocumentStatus, Finding, Principal

from kestrel_app import metrics, runtime
from kestrel_app.components import chips, md, plural, quote, section_label, status_badge
from kestrel_app.metrics import NOT_STORED

FINDING_LABEL = {
    "hidden_text": "Hidden text removed",
    "low_contrast": "Low-contrast text removed",
    "tiny_text": "Tiny text removed",
    "off_page": "Off-page text removed",
    "instruction_like": "Instruction-like text",
    "parse_warning": "Parse warning",
}
DATA_KINDS = {"hidden_text", "low_contrast", "tiny_text", "off_page", "instruction_like"}
SEVERITY = {"high": ("high severity", "red"), "warning": ("warning", "orange"), "info": ("information", "gray")}


def _forget_documents() -> None:
    for prefix in ("docs:", "findings:", "cited:", "compare:", "bots", "audit"):
        runtime.forget(prefix)


def _reingest(svc: AssistantService, who: Principal, bot_id: str) -> None:
    with st.spinner("Re-ingesting the source folder..."):
        report = runtime.attempt(svc.ingest, who, bot_id)
    if report is not None:
        st.session_state.setdefault("ingest_reports", {})[bot_id] = report
        _forget_documents()


def _ingest_report(bot_id: str) -> None:
    report = st.session_state.get("ingest_reports", {}).get(bot_id)
    if report is None:
        return
    with st.expander("Last ingest report", expanded=True, icon=":material/sync:"):
        if not report.files:
            st.caption("No files were found.")
        else:
            counts = Counter(f.action for f in report.files)
            st.caption(", ".join(f"{n} {action}" for action, n in sorted(counts.items())))
            st.dataframe(
                [
                    {
                        "File": f.filename,
                        "Action": f.action,
                        "Status": f.status.value if f.status else "",
                        "Findings": f.findings,
                        "Detail": f.detail or "",
                    }
                    for f in report.files
                ],
                hide_index=True,
            )
        for warning in report.warnings:
            st.warning(warning)


def _metric(column: st.delta_generator.DeltaGenerator, label: str, value: int, caption: str) -> None:
    with column.container(border=True, key=f"mcard-docs-{label}", height="stretch"):
        st.metric(label, value)
        st.caption(caption or " ")


def _metrics(counts: metrics.DocumentCounts) -> None:
    cols = st.columns(5)
    types = " · ".join(f"{t} {n}" for t, n in counts.by_type.items())
    kinds = " · ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.findings_by_kind.items())
    _metric(cols[0], "Indexed", counts.indexed, types or "no documents")
    _metric(cols[1], "Current", counts.current, "searched by default")
    _metric(cols[2], "Superseded", counts.superseded, "only when a question names them")
    _metric(cols[3], "Pending or blocked", counts.pending_or_blocked, "never searched")
    _metric(cols[4], "Findings", counts.findings, kinds or "none")


def _finding(finding: Finding, index: int) -> None:
    with st.container(border=True, key=f"panel-finding-{index}"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.badge(FINDING_LABEL.get(finding.kind, finding.kind), color="orange", icon=":material/warning:")
            label, colour = SEVERITY.get(finding.severity, (finding.severity, "gray"))
            st.badge(label, color=colour)  # type: ignore[arg-type]
        st.caption(finding.locator)
        st.markdown(md(finding.detail))
        if finding.excerpt:
            quote(finding.excerpt, as_data=finding.kind in DATA_KINDS)


def _cited_table(records: list[AuditRecord]) -> None:
    if not records:
        st.caption("No recorded answer has cited this document.")
        return
    st.dataframe(
        [
            {
                "When": r.created_at.strftime("%Y-%m-%d %H:%M"),
                "Outcome": r.entry.outcome.value if r.entry.outcome else "",
                "Channel": r.entry.channel,
                "User": r.entry.user,
                "Question, as screened": r.entry.question_text or NOT_STORED,
                "Trace": r.entry.trace_id,
            }
            for r in records
        ],
        hide_index=True,
    )


def _actions(svc: AssistantService, who: Principal, doc: DocumentRecord) -> None:
    if not runtime.is_operator(who):
        st.caption(f"Blocking, unblocking and re-ingesting need the {OPERATOR_ROLE} role.")
        return
    blocked = doc.status == DocumentStatus.BLOCKED
    verb = "Unblock" if blocked else "Block"
    reason = st.text_input(f"Reason to {verb.lower()} (required, recorded in the audit log)", key=f"reason-{doc.id}")
    if st.button(
        "Unblock this document" if blocked else "Block this document",
        key=f"block-{doc.id}",
        disabled=not reason.strip(),
        type="secondary" if blocked else "primary",
    ):
        action = svc.unblock_document if blocked else svc.block_document
        updated = runtime.attempt(action, doc.id, who, reason.strip())
        if updated is not None:
            st.session_state["doc_notice"] = f"{doc.filename} is now {updated.status.value}."
            _forget_documents()
            st.rerun()


def _details(svc: AssistantService, who: Principal, doc: DocumentRecord, findings: list[Finding], cited: list[AuditRecord] | None) -> None:
    with st.container(border=True, key="panel-details"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.subheader(doc.filename)
            status_badge(doc.status)
        chips([doc.title, f"approved by {doc.approved_by}" if doc.approved_by else "no approval recorded", doc.collection])
        st.caption(f"sha256 `{doc.sha256}` · ingested {doc.ingested_at.strftime('%Y-%m-%d %H:%M')}")
        if doc.status_reason:
            st.caption(f"Status reason: {doc.status_reason}")
        cited_n = "n/a" if cited is None else str(len(cited))
        tab_findings, tab_cited = st.tabs([f"Findings ({len(findings)})", f"Answers that cited it ({cited_n})"])
        with tab_findings:
            if not findings:
                st.caption("The scanner found nothing in this document.")
            for index, finding in enumerate(findings):
                _finding(finding, index)
        with tab_cited:
            if cited is None:
                st.caption("Past answers could not be read.")
            else:
                _cited_table(cited)
        _actions(svc, who, doc)


def _changed_values(svc: AssistantService, who: Principal, bot_id: str, doc: DocumentRecord) -> None:
    """The 'Values that changed' table: numbers that differ between this edition and the one beside it."""
    comparison = runtime.attempt(
        runtime.memo, f"compare:{bot_id}:{doc.id}", lambda: svc.compare_editions(bot_id, doc.id, who)
    )
    if comparison is None:
        return
    section_label("Values that changed")
    if comparison.older is None or comparison.newer is None:
        st.caption(comparison.note or "There is no other edition to compare with.")
        return
    old, new = comparison.older.edition or "older", comparison.newer.edition or "newer"
    st.caption(f"{comparison.older.filename} compared with {comparison.newer.filename}, section by section.")
    if comparison.values:
        st.dataframe(
            [
                {"Section": change.heading.split(" > ")[-1], old: value.before or "(none)", new: value.after or "(none)",
                 "Context": value.context}
                for change, value in comparison.values
            ],
            hide_index=True,
        )
    else:
        st.caption(comparison.note or "No numbers differ between the two editions.")
    other = [c for c in comparison.changes if not c.values]
    if other:
        st.caption("Also changed, wording only or added or removed: " + "; ".join(f"{c.heading.split(' > ')[-1]} ({c.change})" for c in other))


def _history(svc: AssistantService, who: Principal, bot_id: str, documents: list[DocumentRecord], doc: DocumentRecord) -> None:
    editions = metrics.edition_history(documents, doc)
    with st.container(border=True, key="panel-history"):
        st.subheader(f"Edition history: {doc.family}" if doc.family else "Edition history")
        if not editions:
            st.caption("This document has no other editions.")
            return
        for index, edition in enumerate(editions):
            with st.container(border=True, key=f"edition-{index}"):
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.markdown(f"**{md(edition.edition or 'undated')}**")
                    status_badge(edition.status)
                nxt = editions[index + 1] if index + 1 < len(editions) else None
                start = edition.effective_from.isoformat() if edition.effective_from else "an unknown date"
                span = f"in force from {start}"
                if nxt is not None and nxt.effective_from:
                    span += f"; replaced by {nxt.edition} from {nxt.effective_from.isoformat()}"
                st.caption(f"{edition.filename} · {span}")
        current = next((e for e in editions if e.status == DocumentStatus.CURRENT), None)
        if current is not None:
            st.caption(f"Answers use the {current.edition} edition unless a question names another.")
        _changed_values(svc, who, bot_id, doc)


ctx = runtime.context()
if ctx is None:
    st.stop()
svc, who, bot = ctx
operator = runtime.is_operator(who)

with st.container(horizontal=True, vertical_alignment="center"):
    st.title("Documents and findings")
    chips([f"Collection: {c}" for c in bot.collections])
    st.space("stretch")
    if operator and st.button("Re-ingest folder", icon=":material/sync:"):
        _reingest(svc, who, bot.id)
if notice := st.session_state.pop("doc_notice", None):
    st.success(notice, icon=":material/check:")
_ingest_report(bot.id)

documents = runtime.attempt(runtime.memo, f"docs:{bot.id}", lambda: svc.list_documents(bot.id, who))
if documents is None:
    st.stop()
all_findings = runtime.attempt(runtime.memo, f"findings:{bot.id}", lambda: svc.list_findings(bot.id, who), default=[]) or []
_metrics(metrics.document_counts(documents, all_findings))

if not documents:
    hint = " Use Re-ingest folder to read the source folder." if operator else " An operator can ingest the source folder."
    st.info(f"No documents have been ingested for {bot.name} yet.{hint}", icon=":material/folder_open:")
    st.stop()

by_doc = Counter(f.document_id for f in all_findings)
cited: dict[str, list[AuditRecord] | None] = {}
for d in documents:
    try:
        cited[d.id] = runtime.memo(f"cited:{d.id}", lambda d=d: svc.affected_answers(d.id, who))
    except Exception:  # noqa: BLE001 - shown as n/a; the details panel names the failure
        cited[d.id] = None

event = st.dataframe(
    [
        {
            "Document": d.filename,
            "Title": d.title,
            "Edition": d.edition or "undated",
            "Status": d.status.value,
            "Effective from": d.effective_from.isoformat() if d.effective_from else "",
            "Sections": d.section_count,
            "Chunks": d.chunk_count,
            "Findings": by_doc.get(d.id, d.finding_count),
            "Times cited": None if cited[d.id] is None else len(cited[d.id] or []),
        }
        for d in documents
    ],
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key=f"documents-{bot.id}",
)
rows = event.selection.rows if event is not None else []
chosen = documents[rows[0]] if rows and rows[0] < len(documents) else documents[0]
st.caption(f"Select a row to see its details. Showing {chosen.filename}. {plural(len(documents), 'document')} in total.")

main_col, side_col = st.columns([2, 1], gap="medium")
with main_col:
    section_label("Document details")
    _details(svc, who, chosen, [f for f in all_findings if f.document_id == chosen.id], cited[chosen.id])
with side_col:
    _history(svc, who, bot.id, documents, chosen)
