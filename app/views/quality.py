"""Quality and audit: live usage from the audit log, evaluation runs, and the audit trail."""

from __future__ import annotations

import streamlit as st

from kestrel.contracts import OPERATOR_ROLE, AssistantService, AuditRecord, BotSummary, Outcome, Principal

from kestrel_app import eval_view, metrics, runtime
from kestrel_app.components import fmt_cost, fmt_latency, fmt_share, plural, section_label

# Audit records read for the live usage tab (the most recent ones): the most the service returns at once.
USAGE_LIMIT = 500


def _tail(svc: AssistantService, who: Principal, bot_id: str | None, limit: int) -> list[AuditRecord]:
    key = f"audit:{bot_id or '*'}:{limit}"
    return runtime.attempt(runtime.memo, key, lambda: svc.audit_tail(who, limit=limit, bot_id=bot_id), default=[], ttl_s=20) or []


def _card(column: st.delta_generator.DeltaGenerator, label: str, value: str, caption: str) -> None:
    with column.container(border=True, key=f"mcard-usage-{label}", height="stretch"):
        st.metric(label, value)
        st.caption(caption)


def _live_usage(svc: AssistantService, who: Principal, bot: BotSummary, everyone: bool) -> None:
    records = _tail(svc, who, None if everyone else bot.id, USAGE_LIMIT)
    usage = metrics.live_usage(records)
    if usage.questions == 0:
        st.info("No questions have been recorded in the audit log yet.", icon=":material/insights:")
        return
    assert usage.first is not None and usage.last is not None
    st.caption(
        f"{plural(usage.questions, 'question')} from {usage.first:%Y-%m-%d} to {usage.last:%Y-%m-%d}, "
        f"read from the {USAGE_LIMIT} most recent audit records."
    )
    q = usage.questions
    answered, partial = usage.count(Outcome.ANSWERED), usage.count(Outcome.PARTIAL)
    refused, errors = usage.count(Outcome.REFUSED), usage.count(Outcome.ERROR)
    cols = st.columns(6)
    _card(cols[0], "Questions", str(q), " · ".join(f"{c} {n}" for c, n in usage.by_channel.items()))
    _card(cols[1], "Answered", str(answered), f"{fmt_share(answered, q)} · {partial} partial")
    _card(cols[2], "Not in documents", str(usage.count(Outcome.NOT_FOUND)), f"{fmt_share(usage.count(Outcome.NOT_FOUND), q)} · declined with a reason")
    _card(cols[3], "Withheld", str(usage.count(Outcome.WITHHELD)), f"{fmt_share(usage.count(Outcome.WITHHELD), q)} · failed evidence checks")
    _card(cols[4], "Refused or error", str(refused + errors), f"{refused} refused · {errors} errors")
    cost = fmt_cost(usage.median_cost_usd) if usage.median_cost_usd is not None else "unknown"
    unpriced = f" · {usage.unpriced} unpriced" if usage.unpriced else ""
    _card(cols[5], "Median cost and time", cost, f"{fmt_latency(usage.median_latency_ms)} per question{unpriced}")

    section_label("Most cited sections")
    cited = metrics.most_cited_sections(records)
    if not cited:
        st.caption("No answer has cited a section yet.")
    else:
        top = cited[0].count
        st.dataframe(
            [{"Section": c.locator, "Answers citing it": c.count} for c in cited],
            hide_index=True,
            column_config={
                "Answers citing it": st.column_config.ProgressColumn(min_value=0, max_value=top, format="%d"),
            },
        )

    rows = metrics.unanswered_questions(records)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader("Questions the assistant could not answer")
        st.space("stretch")
        st.download_button(
            "Export for content owners",
            metrics.unanswered_csv(rows, with_closest=True),
            file_name="unanswered_questions.csv",
            mime="text/csv",
            disabled=not rows,
            icon=":material/download:",
        )
    if not rows:
        st.caption("Every recorded question was answered.")
        return
    st.dataframe(
        [
            {
                "Question, as screened": r.question,
                "Asked": r.count,
                "Outcome": r.outcome.replace("_", " "),
                "Why": r.reason,
                "Closest section": r.closest,
                "Last asked": r.last_asked.strftime("%Y-%m-%d %H:%M"),
            }
            for r in rows
        ],
        hide_index=True,
    )
    st.caption(
        '"Not in documents" rows are content gaps for the business. "Withheld" rows are engineering '
        f"defects and are reviewed first. {metrics.NOT_STORED} means this assistant keeps only a digest of the "
        "question, so a reason written from it is not kept either. The closest section is the top-ranked passage retrieved."
    )


def _audit_trail(svc: AssistantService, who: Principal, bot: BotSummary, everyone: bool) -> None:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        limit = st.selectbox("Records to show", [50, 200, USAGE_LIMIT], key="audit_limit")
        st.space("stretch")
        verify = st.button("Verify chain", icon=":material/link:", disabled=not runtime.is_operator(who))
    if not runtime.is_operator(who):
        st.caption(f"Verifying the hash chain needs the {OPERATOR_ROLE} role.")
    elif verify:
        result = runtime.attempt(svc.audit_verify, who)
        if result is not None:
            runtime.forget("audit_verify")
            st.session_state["verify_result"] = result
    result = st.session_state.get("verify_result")
    if result is not None:
        if result.ok:
            st.success(f"Chain intact: {plural(result.checked, 'record')} checked.", icon=":material/check:")
        else:
            where = f" at record {result.first_broken_id}" if result.first_broken_id is not None else ""
            st.error(f"Chain broken{where}. {result.detail or ''}".strip(), icon=":material/link_off:")

    records = _tail(svc, who, None if everyone else bot.id, int(limit))
    if not records:
        st.info("The audit log has no records yet.", icon=":material/history:")
        return
    st.dataframe(
        [
            {
                "When": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "Kind": r.entry.kind,
                "Outcome": r.entry.outcome.value if r.entry.outcome else "",
                "Assistant": r.entry.bot_id or "",
                "Channel": r.entry.channel,
                "User": r.entry.user,
                "Model": r.entry.model or "",
                "Latency": fmt_latency(r.entry.latency_ms) if r.entry.kind == "ask" else "",
                "Cost": fmt_cost(r.entry.cost_usd) if r.entry.kind == "ask" else "",
                "Citations": len(r.entry.citations),
                "Detail": r.entry.detail or (r.entry.error_category.value if r.entry.error_category else ""),
                "Trace": r.entry.trace_id,
            }
            for r in records
        ],
        hide_index=True,
    )


ctx = runtime.context()
if ctx is None:
    st.stop()
svc, who, bot = ctx

with st.container(horizontal=True, vertical_alignment="center"):
    st.title("Quality and audit")
    st.space("stretch")
    everyone = st.toggle("All assistants I can see", key="quality_all", help=f"Off: only {bot.name}.")

usage_tab, eval_tab, audit_tab = st.tabs(["Live usage", "Evaluation runs", "Audit trail"])
with usage_tab:
    _live_usage(svc, who, bot, everyone)
with eval_tab:
    eval_view.render(runtime.results_root())
with audit_tab:
    _audit_trail(svc, who, bot, everyone)
