"""Shared display pieces: the small CSS block, badges, chips and formatting.

Colour is never the only signal: every badge carries words, and grid cells carry a
letter explained by a legend.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

import streamlit as st

from kestrel.contracts import AskResult, CheckIssue, DocumentStatus, EvidenceReport, Outcome, repaired_detail

CSS = """
<style>
.k-chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 4px}
.k-chip{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;font-size:12px;
  font-weight:500;border:1px solid #D5DBE3;background:#fff;color:#3E4955;white-space:nowrap}
.k-chip.k-label{font-family:ui-monospace,monospace;background:#EEF3FB;border-color:#C9D7EE;color:#143F82}
.k-lbl{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#5B6673;margin:6px 0 2px}
.k-calc{background:#F7F8FA;border-radius:8px;padding:10px 14px;margin:4px 0}
.k-calc code{font-size:14px;background:none}
.k-quote{margin:4px 0;padding:8px 12px;border-left:3px solid #1F5FBF;background:#F7F8FA;font-size:13px;
  line-height:1.5;white-space:pre-wrap}
.k-data{margin:4px 0;padding:8px 12px;background:#FFF9F1;border:1px dashed #B4540A;border-radius:6px;
  font-family:ui-monospace,monospace;font-size:12px;line-height:1.5;color:#3E4955;white-space:pre-wrap}
.k-data::before{content:"Quoted document text, shown as data only";display:block;font-family:system-ui,sans-serif;
  font-size:11px;font-weight:600;color:#8A3F00;margin-bottom:4px}
.k-legend{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:#3E4955;margin:4px 0}
.k-cell{display:inline-block;width:20px;height:20px;border-radius:5px;text-align:center;line-height:20px;
  font-size:12px;font-weight:600;margin-right:4px}
</style>
"""

OUTCOME_BADGE: dict[Outcome, tuple[str, str, str]] = {
    Outcome.ANSWERED: ("Answered", "blue", ":material/chat:"),
    Outcome.PARTIAL: ("Partial answer", "orange", ":material/rule:"),
    Outcome.NOT_FOUND: ("Not in the documents", "orange", ":material/search_off:"),
    Outcome.REFUSED: ("Refused", "gray", ":material/block:"),
    Outcome.WITHHELD: ("Withheld", "red", ":material/visibility_off:"),
    Outcome.ERROR: ("Error", "red", ":material/error:"),
}

CHECK_LABELS: dict[str, str] = {
    "schema": "Answer format",
    "label_retrieved": "Cited excerpt was retrieved",
    "quote_verbatim": "Quote matches the source word for word",
    "table_row_cited": "Table facts cite their row",
    "numbers_grounded": "Numbers appear in a cited source",
    "calculation_recomputed": "Calculation recomputed by the application",
    "decline_reason": "A decline gives its reason",
    "source_status": "Cited source is approved and in scope",
}
EVIDENCE_CHECKS = 7
"""The evidence checks after the schema check (DESIGN.md section 6)."""

STATUS_BADGE: dict[DocumentStatus, tuple[str, str]] = {
    DocumentStatus.CURRENT: ("current", "blue"),
    DocumentStatus.SUPERSEDED: ("superseded", "gray"),
    DocumentStatus.PENDING: ("pending", "orange"),
    DocumentStatus.BLOCKED: ("blocked", "red"),
}

GRID_COLOURS: dict[str, tuple[str, str]] = {
    "C": ("#D5E6F7", "#143F82"),
    "D": ("#E3F1F7", "#0B4F6C"),
    "P": ("#FFE2BF", "#8A3F00"),
    "N": ("#FFE2BF", "#8A3F00"),
    "X": ("#8A3F00", "#FFFFFF"),
    "W": ("#8A3F00", "#FFFFFF"),
    "M": ("#F4D6D6", "#7A1F1F"),
    "E": ("#5B6673", "#FFFFFF"),
    "?": ("#EEF1F5", "#3E4955"),
}

ATTESTATION = (
    '"Evidence checks passed" means every quote was found word for word in an approved document '
    "that was in scope, the numbers appear in the cited sources and any arithmetic was recomputed "
    "by the application. It does not mean the answer is right: read the quotes to confirm they "
    "support it."
)


def inject_css() -> None:
    """Add the app's small style block once per run."""
    st.markdown(CSS, unsafe_allow_html=True)


def esc(text: object) -> str:
    """HTML-escape text for the few places raw HTML is used."""
    return html.escape(str(text), quote=True)


def md(text: object) -> str:
    """Text safe to pass to `st.markdown`: no raw HTML, no LaTeX from `$` signs."""
    return html.escape(str(text), quote=False).replace("$", "\\$")


def chips(items: Iterable[str], label_style: bool = False) -> None:
    """A row of small chips (text only)."""
    cls = "k-chip k-label" if label_style else "k-chip"
    body = "".join(f'<span class="{cls}">{esc(item)}</span>' for item in items if item)
    if body:
        st.markdown(f'<div class="k-chips">{body}</div>', unsafe_allow_html=True)


def section_label(text: str) -> None:
    """Small upper-case label above a group."""
    st.markdown(f'<div class="k-lbl">{esc(text)}</div>', unsafe_allow_html=True)


def quote(text: str, as_data: bool = False) -> None:
    """A verbatim quote; `as_data` draws hidden or suspicious text in a distinct, labelled box."""
    cls = "k-data" if as_data else "k-quote"
    st.markdown(f'<div class="{cls}">{esc(text)}</div>', unsafe_allow_html=True)


def outcome_badge(outcome: Outcome) -> None:
    label, colour, icon = OUTCOME_BADGE.get(outcome, (outcome.value, "gray", ":material/help:"))
    st.badge(label, color=colour, icon=icon)  # type: ignore[arg-type]


def failed_checks(report: EvidenceReport | None) -> list[CheckIssue]:
    """Issues that still fail. Warnings, including problems fixed by the repair turn, are not failures."""
    return list(report.failures) if report else []


def repaired_line(issue: CheckIssue) -> str:
    """A problem the repair turn fixed, in words, without the stored `Before repair:` marker."""
    name = CHECK_LABELS.get(issue.check, issue.check)
    where = f" ({issue.label})" if issue.label else ""
    return f"{name}{where}: {repaired_detail(issue)}"


def evidence_badge(result: AskResult) -> None:
    """The evidence-check result, in words."""
    report = result.evidence_report
    if report is None:
        if result.outcome in {Outcome.ANSWERED, Outcome.PARTIAL}:
            st.badge("Evidence checks not run", color="gray", icon=":material/help:")
        return
    if report.passed:
        suffix = " after one repair" if report.repaired else ""
        st.badge(f"Evidence checks passed{suffix}", color="blue", icon=":material/check:")
    else:
        st.badge("Evidence checks failed", color="red", icon=":material/close:")


def checks_passed_text(report: EvidenceReport | None) -> str:
    """For example `7 of 7 passed`, or `not run`."""
    if report is None:
        return "not run"
    failing = {i.check for i in failed_checks(report) if i.check != "schema"}
    text = f"{EVIDENCE_CHECKS - len(failing)} of {EVIDENCE_CHECKS} passed"
    if any(i.check == "schema" for i in failed_checks(report)):
        text += "; the answer format was invalid"
    return text + (" after a repair turn" if report.repaired else "")


def issue_line(issue: CheckIssue) -> str:
    """One failed check in words: which check, about which label, and why."""
    name = CHECK_LABELS.get(issue.check, issue.check)
    where = f" ({issue.label})" if issue.label else ""
    return f"{name}{where}: {issue.detail}"


def status_badge(status: DocumentStatus) -> None:
    label, colour = STATUS_BADGE.get(status, (status.value, "gray"))
    st.badge(label, color=colour)  # type: ignore[arg-type]


def model_locality(model: str) -> str:
    """`on-device` for a local Ollama model, otherwise `external`."""
    return "on-device" if model.split(":", 1)[0] == "ollama" else "external"


def fmt_latency(ms: float | None) -> str:
    if ms is None:
        return "n/a"
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def fmt_cost(usd: float | None) -> str:
    if usd is None:
        return "cost unknown"
    return f"US${usd:.4f}" if usd < 0.01 else f"US${usd:.3f}"


def fmt_share(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "n/a"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def legend(letters: Iterable[tuple[str, str]]) -> None:
    """Legend for the per-question grid: coloured letter plus its meaning."""
    parts = []
    for letter, meaning in letters:
        bg, fg = GRID_COLOURS.get(letter, GRID_COLOURS["?"])
        parts.append(
            f'<span><span class="k-cell" style="background:{bg};color:{fg}">{esc(letter)}</span>{esc(meaning)}</span>'
        )
    st.markdown(f'<div class="k-legend">{"".join(parts)}</div>', unsafe_allow_html=True)
