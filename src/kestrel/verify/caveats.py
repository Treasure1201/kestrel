"""Drop caveats that the evidence does not support, instead of failing the whole answer.

Caveats are optional commentary. A caveat that quotes a number no cited excerpt, tool
result, the question or a recomputed calculation supports, that names a label the
answer does not cite, or that speaks about drafts, checks or corrections, is removed before the evidence checks decide pass or fail. Each
removal is reported as a warning-severity `CheckIssue`, so it is never silent. The
answer text, the citations, the calculations and the reason are never changed: a
number in the answer that is not grounded still fails `numbers_grounded`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from kestrel.agent.calculator import Calculation
from kestrel.contracts import AgentAnswer, CheckIssue, Evidence, ScopeDecision, ToolResult
from kestrel.verify.checks import _Context, check_calculations, check_labels, check_numbers, clean_label, self_reference

_LABEL_MENTION = re.compile(r"(?<![\w.])\[?([ct][1-9][0-9]*(?:\.r[1-9][0-9]*)?)\]?(?![\w])", re.IGNORECASE)
_PREVIEW = 100


def _preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _PREVIEW else text[: _PREVIEW - 3] + "..."


def _uncited_labels(caveat: str, cited: set[str], known: set[str]) -> list[str]:
    """Labels shown in this request that the caveat names but the answer does not cite."""
    named = {clean_label(match.group(1)) for match in _LABEL_MENTION.finditer(caveat)}
    return sorted(label for label in named if label in known and label not in cited)


def prune_caveats(
    question: str,
    answer: AgentAnswer,
    evidence: Mapping[str, Evidence],
    scope: ScopeDecision | None,
    tool_calculations: Sequence[Calculation] = (),
    tool_results: Mapping[str, ToolResult] | None = None,
) -> tuple[AgentAnswer, list[CheckIssue]]:
    """The answer without its unsupported caveats, and one warning per caveat removed.

    A caveat is judged alone, against the answer's own citations and calculations,
    exactly as `numbers_grounded` would judge it.
    """
    if not answer.caveats:
        return answer, []
    tools = dict(tool_results or {})
    ctx = _Context(question=question, answer=answer, evidence=evidence, scope=scope,
                   tool_calculations=tool_calculations, tools=tools)
    check_labels(ctx)
    check_calculations(ctx)
    cited = {label for label, _ in ctx.cited} | {label for label, _ in ctx.cited_tools}
    known = set(evidence) | set(tools)
    kept: list[str] = []
    issues: list[CheckIssue] = []
    for caveat in answer.caveats:
        ctx.answer = answer.model_copy(update={"answer": "", "caveats": [caveat]})
        if check_numbers(ctx):
            issues.append(CheckIssue(
                check="numbers_grounded", severity="warning",
                detail=f"Caveat removed: it quoted a figure from a source the answer did not cite: \"{_preview(caveat)}\"",
            ))
            continue
        phrase = self_reference(caveat)
        if phrase:
            issues.append(CheckIssue(
                check="schema", severity="warning",
                detail=f"Caveat removed: it spoke about \"{phrase}\" rather than the question: \"{_preview(caveat)}\"",
            ))
            continue
        uncited = _uncited_labels(caveat, cited, known)
        if uncited:
            issues.append(CheckIssue(
                check="label_retrieved", severity="warning", label=uncited[0],
                detail=f"Caveat removed: it named {', '.join(uncited)}, which the answer did not cite: \"{_preview(caveat)}\"",
            ))
            continue
        kept.append(caveat)
    if not issues:
        return answer, []
    return answer.model_copy(update={"caveats": kept}), issues


def pruned_summary(issues: Sequence[CheckIssue]) -> str:
    """The detail of the answer step that records removed caveats: `1 caveat removed: it quoted ...`."""
    reasons = "; ".join(issue.detail.removeprefix("Caveat removed: ").split(": \"", 1)[0] for issue in issues)
    return f"{len(issues)} caveat{'s' if len(issues) != 1 else ''} removed: {reasons}"
