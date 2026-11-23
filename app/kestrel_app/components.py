"""Shared display pieces: badges, chips, quotes and formatting. The style sheet is in `theme.py`.

Colour is never the only signal: every badge carries words, and grid cells carry a
letter explained by a legend.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

import streamlit as st

from kestrel.contracts import AskResult, CheckIssue, DocumentStatus, EvidenceReport, Outcome, repaired_detail

from kestrel_app.theme import CSS


OUTCOME_BADGE: dict[Outcome, tuple[str, str, str]] = {
    Outcome.ANSWERED: ("Answered", "green", ":material/check_circle:"),
    Outcome.PARTIAL: ("Partial answer", "blue", ":material/rule:"),
    Outcome.NOT_FOUND: ("Not in the documents", "orange", ":material/search_off:"),
    Outcome.REFUSED: ("Refused", "gray", ":material/block:"),
    Outcome.WITHHELD: ("Withheld", "red", ":material/visibility_off:"),
    Outcome.ERROR: ("Error", "red", ":material/error:"),
}

OUTCOME_TONE: dict[Outcome, str] = {
    Outcome.ANSWERED: "ok",
    Outcome.PARTIAL: "info",
    Outcome.NOT_FOUND: "warn",
    Outcome.REFUSED: "neutral",
    Outcome.WITHHELD: "bad",
    Outcome.ERROR: "bad",
}
"""The card tone for each outcome; it always comes with the badge word."""

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
    "C": ("#E2F5F0", "#0B7A66"),
    "D": ("#E7F0FB", "#0B4F9E"),
    "P": ("#FFF3D1", "#7A4B00"),
    "N": ("#FFF3D1", "#7A4B00"),
    "X": ("#A1301C", "#FFFFFF"),
    "W": ("#A1301C", "#FFFFFF"),
    "M": ("#FDEBE7", "#A1301C"),
    "E": ("#3C4A5C", "#FFFFFF"),
    "?": ("#EAF0F6", "#3C4A5C"),
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


def html_text(text: object, strip: bool = True) -> str:
    """Text for inside a raw HTML block: escaped, with line breaks as `<br>` so no blank line ends the block."""
    raw = str(text).strip() if strip else str(text)
    return "<br>".join(html.escape(raw, quote=False).splitlines())


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


DATA_NOTE = "Quoted document text, shown as data only"


def quote(text: str, as_data: bool = False, note: str = DATA_NOTE, highlight: str = "") -> None:
    """A verbatim quote; `as_data` draws hidden, suspicious or tool text in a distinct, labelled box.

    `highlight` marks the first case-insensitive occurrence of that text inside the quote.
    """
    if as_data:
        st.markdown(f'<div class="k-data" data-note="{esc(note)}">{html_text(text)}</div>', unsafe_allow_html=True)
        return
    st.markdown(f'<div class="k-quote">{marked(text, highlight)}</div>', unsafe_allow_html=True)


def marked(text: str, part: str) -> str:
    """Escaped `text` with the first occurrence of `part` wrapped in `<mark>`."""
    at = text.lower().find(part.lower().strip()) if part.strip() else -1
    if at < 0:
        return html_text(text)
    stop = at + len(part.strip())
    before, inside, after = (html_text(piece, strip=False) for piece in (text[:at], text[at:stop], text[stop:]))
    return f"{before}<mark>{inside}</mark>{after}"


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
        st.badge(f"Evidence checks passed{suffix}", color="green", icon=":material/check:")
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
