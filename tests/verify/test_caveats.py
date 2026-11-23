"""Unsupported caveats are dropped and recorded; the answer itself is never relaxed."""

from __future__ import annotations

from verify.builders import SCOPE, answer, prose

from kestrel.verify import run_checks
from kestrel.verify.caveats import prune_caveats, pruned_summary

WAITING = prose("c1", "The waiting period is three months.")
OTHER = prose("c2", "Members may join up to age 64.")
EVIDENCE = {"c1": WAITING, "c2": OTHER}
QUESTION = "What is the waiting period?"


def _prune(ans):  # type: ignore[no-untyped-def]
    return prune_caveats(QUESTION, ans, EVIDENCE, SCOPE)


def test_grounded_caveat_is_kept() -> None:
    ans = answer("Three months.", [("c1", "the waiting period is three months")], caveats=["The three months run from the start date."])
    pruned, issues = _prune(ans)
    assert pruned == ans and issues == []


def test_caveat_with_a_figure_from_an_uncited_excerpt_is_dropped_and_recorded() -> None:
    kept = "The period may differ for other options."
    ans = answer("Three months.", [("c1", "the waiting period is three months")], caveats=[kept, "Entry is limited to age 64."])
    assert "numbers_grounded" in [i.check for i in run_checks(QUESTION, ans, EVIDENCE, SCOPE)]
    pruned, issues = _prune(ans)
    assert pruned.caveats == [kept]
    assert (pruned.answer, pruned.citations, pruned.reason, pruned.status) == (ans.answer, ans.citations, ans.reason, ans.status)
    assert len(issues) == 1 and issues[0].severity == "warning" and "64" in issues[0].detail
    assert pruned_summary(issues) == "1 caveat removed: it quoted a figure from a source the answer did not cite"
    assert run_checks(QUESTION, pruned, EVIDENCE, SCOPE) == []


def test_caveat_naming_an_uncited_label_is_dropped() -> None:
    ans = answer("Three months.", [("c1", "the waiting period is three months")], caveats=["c2 is about joining, not waiting."])
    pruned, issues = _prune(ans)
    assert pruned.caveats == [] and issues[0].check == "label_retrieved" and issues[0].label == "c2"


def test_caveat_about_drafts_is_dropped() -> None:
    ans = answer("Three months.", [("c1", "the waiting period is three months")], caveats=["The first draft contradicted itself."])
    pruned, issues = _prune(ans)
    assert pruned.caveats == [] and len(issues) == 1


def test_an_ungrounded_number_in_the_answer_still_fails() -> None:
    ans = answer("Three months, and entry is limited to age 64.", [("c1", "the waiting period is three months")])
    pruned, issues = _prune(ans)
    assert pruned == ans and issues == []
    assert "numbers_grounded" in [i.check for i in run_checks(QUESTION, pruned, EVIDENCE, SCOPE)]


def test_the_reason_is_never_pruned() -> None:
    ans = answer("The documents do not state it.", status="not_found", reason="Only age 64 is mentioned.", caveats=["Age 64 is stated."])
    pruned, issues = _prune(ans)
    assert pruned == ans and issues == []  # a decline may draw on every excerpt shown
